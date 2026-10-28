from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Callable, DefaultDict, List, Optional

from .errors import CancelledError, HttpError, ProviderError, is_retryable
from .format import format_duration
from .http import HttpClient, file_name_from_url, parse_content_disposition
from .paths import safe_file_name, safe_relative_dir
from .source import Located, Source
from .speed import SpeedTracker

log = logging.getLogger(__name__)

_REFRESH_STATUSES = frozenset({401, 403, 404, 410})
_SETTLED_STATES = frozenset({"completed", "failed", "cancelled"})
_MAX_BACKOFF_SECONDS = 30.0
_MAX_RETRY_AFTER_SECONDS = 60.0
_BASE_BACKOFF_SECONDS = 1.0
_WEB_CONTENT = re.compile(r"^(text/html|application/json)", re.IGNORECASE)
_WEB_EXTENSION = re.compile(r"\.(html?|json)$", re.IGNORECASE)


@dataclass(frozen=True)
class Progress:
    id: str
    state: str
    file_name: str
    downloaded: int
    total: Optional[int]
    percent: Optional[float]
    remaining: Optional[int]
    speed: float
    eta: Optional[float]
    eta_text: str


class Download:
    def __init__(self, source: Source, *, directory: str = ".", filename: Optional[str] = None, retries: int = 3,
                 progress_interval: float = 0.25, client: Optional[Any] = None,
                 resolve_again: Optional[Callable[[], List[Source]]] = None, allow_html: bool = False):
        self._source = source
        self._directory = directory
        self._filename = filename
        self._retries = retries
        self._interval = progress_interval
        self._client = client or HttpClient()
        self._resolve_again = resolve_again
        self._allow_html = allow_html

        self._lock = threading.RLock()
        self._state = "queued"
        self._intent: Optional[str] = None
        self._response: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._interrupt = threading.Event()
        self._done = threading.Event()
        self._listeners: DefaultDict[str, List[Callable[..., None]]] = defaultdict(list)

        self._downloaded = 0
        self._total: Optional[int] = source.size
        self._tracker = SpeedTracker()
        self._last_emit = 0.0
        self._server_name: Optional[str] = None
        self._error: Optional[BaseException] = None
        self._result: Optional[str] = None
        self.id = source.id or source.url


    @property
    def state(self) -> str:
        return self._state

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    @property
    def file_name(self) -> str:
        return safe_file_name(
            self._filename or self._source.filename or self._server_name or file_name_from_url(self._source.url)
        )

    @property
    def file_path(self) -> str:
        return os.path.join(self._directory, safe_relative_dir(self._source.subdir), self.file_name)

    @property
    def progress(self) -> Progress:
        state = self._state
        speed = self._tracker.bytes_per_second if state == "downloading" else 0.0
        total, downloaded = self._total, self._downloaded
        remaining = None if total is None else max(total - downloaded, 0)
        if state == "completed":
            eta: Optional[float] = 0.0
        elif remaining is not None and speed > 0:
            eta = remaining / speed
        else:
            eta = None
        return Progress(
            id=self.id,
            state=state,
            file_name=self.file_name,
            downloaded=downloaded,
            total=total,
            percent=min(downloaded / total * 100, 100.0) if total else None,
            remaining=remaining,
            speed=speed,
            eta=eta,
            eta_text=format_duration(eta),
        )

    def on(self, event: str, callback: Callable[..., None]) -> "Download":
        self._listeners[event].append(callback)
        return self

    def start(self) -> "Download":
        self._launch("queued")
        return self

    def wait(self, timeout: Optional[float] = None) -> str:
        if not self._done.wait(timeout):
            raise TimeoutError("Download still running")
        if self._error is not None:
            raise self._error
        return self._result or self.file_path

    def pause(self) -> bool:
        with self._lock:
            if self._state == "queued":
                response = None
            elif self._state == "downloading":
                self._intent = "pause"
                self._interrupt.set()
                response = self._response
            else:
                return False
        self._set_state("paused")
        if response is not None:
            response.close()
        return True

    def resume(self) -> bool:
        if self._state != "paused":
            return False
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        return self._launch("paused")

    def cancel(self, delete_file: bool = True) -> bool:
        with self._lock:
            if self._state in _SETTLED_STATES:
                return False
            self._intent = "cancel"
            self._interrupt.set()
            self._error = CancelledError()
            response = self._response
        self._set_state("cancelled")
        if response is not None:
            response.close()
        self._done.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        if delete_file:
            try:
                os.remove(f"{self.file_path}.part")
            except FileNotFoundError:
                pass
        return True


    def _launch(self, expected_state: str) -> bool:
        with self._lock:
            if self._state != expected_state:
                return False
            self._intent = None
            self._interrupt.clear()
            self._tracker.reset()
            self._last_emit = 0.0
            self._state = "downloading"
            self._thread = threading.Thread(target=self._run, name=f"udl-{self.id[:8]}", daemon=True)
        self._emit("state", "downloading")
        self._thread.start()
        return True

    def _set_state(self, state: str) -> None:
        with self._lock:
            if self._state == state:
                return
            self._state = state
        self._emit("state", state)

    def _emit(self, event: str, *args: Any) -> None:
        for callback in list(self._listeners[event]):
            try:
                callback(*args)
            except Exception:
                log.exception("Listener for %r raised", event)

    def _run(self) -> None:
        try:
            path = self._run_with_retries()
        except Exception as error:
            self._settle(None, error)
            return
        self._settle(path, None)

    def _settle(self, path: Optional[str], error: Optional[BaseException]) -> None:
        intent = self._intent
        if intent == "cancel":
            return
        if intent == "pause" and path is None:
            return
        if error is not None:
            self._error = error
            self._set_state("failed")
            self._emit("failed", error)
            self._done.set()
            return
        self._result = path
        if self._total is None:
            self._total = self._downloaded
        self._downloaded = self._total
        self._set_state("completed")
        self._emit("progress", self.progress)
        self._emit("completed", path)
        self._done.set()

    def _run_with_retries(self) -> Optional[str]:
        attempt = 0
        refreshed = False
        while True:
            try:
                return self._transfer()
            except Exception as error:
                if self._intent:
                    return None
                if (not refreshed and self._resolve_again is not None and isinstance(error, HttpError)
                        and error.status in _REFRESH_STATUSES):
                    refreshed = True
                    self._refresh_source()
                    continue
                attempt += 1
                if attempt > self._retries or not is_retryable(error):
                    raise
                self._interrupt.wait(self._backoff(attempt, error))
                if self._intent:
                    return None

    @staticmethod
    def _backoff(attempt: int, error: BaseException) -> float:
        if isinstance(error, HttpError) and error.retry_after is not None:
            return min(error.retry_after, _MAX_RETRY_AFTER_SECONDS)
        return min(_BASE_BACKOFF_SECONDS * 2 ** (attempt - 1), _MAX_BACKOFF_SECONDS) + random.random() * 0.5

    def _refresh_source(self) -> None:
        assert self._resolve_again is not None
        sources = self._resolve_again()
        match = next((candidate for candidate in sources if candidate.id == self._source.id), None)
        if match is None and len(sources) == 1:
            match = sources[0]
        if match is None:
            raise ProviderError("The file is no longer available at the source")
        self._source = replace(self._source, url=match.url, headers=match.headers, resolve_url=match.resolve_url,
                               size=match.size or self._source.size)

    def _locate(self) -> Located:
        if self._source.resolve_url is None:
            return Located(self._source.url, dict(self._source.headers))
        return self._source.resolve_url(self._client)

    def _transfer(self) -> Optional[str]:
        part_path = f"{self.file_path}.part"
        os.makedirs(os.path.dirname(part_path) or ".", exist_ok=True)

        offset = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        target = self._locate()
        headers = {"Accept-Encoding": "identity", **target.headers}
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"

        try:
            response = self._client.open(target.url, headers=headers)
        except HttpError as error:
            if error.status == 416 and offset > 0:
                self._downloaded = offset
                return self._finalize(part_path)
            raise

        with self._lock:
            self._response = response
            interrupted = self._intent is not None
        try:
            if interrupted:
                return None
            self._reject_web_page(response)
            if offset > 0 and response.status != 206:
                offset = 0
            self._server_name = self._server_name or parse_content_disposition(response.headers.get("content-disposition"))
            self._downloaded = offset
            self._total = _total_from(response, offset) or self._source.size

            self._tracker.record(self._downloaded)
            with open(part_path, "ab" if offset > 0 else "wb") as output:
                for chunk in response.iter_chunks():
                    output.write(chunk)
                    self._downloaded += len(chunk)
                    self._tick()
        finally:
            response.close()
            with self._lock:
                self._response = None

        if self._intent:
            return None
        if self._total is not None and self._downloaded < self._total:
            raise ConnectionError(f"Connection closed early ({self._downloaded}/{self._total} bytes)")
        return self._finalize(part_path)

    def _reject_web_page(self, response: Any) -> None:
        is_web = bool(_WEB_CONTENT.match(response.headers.get("content-type", "")))
        if is_web and not (self._allow_html or _WEB_EXTENSION.search(self.file_name)):
            raise ProviderError(
                "The server returned a web page or API response instead of the file "
                "(link expired, private, or the host changed its API)"
            )

    def _finalize(self, part_path: str) -> str:
        destination = self.file_path
        os.replace(part_path, destination)
        return destination

    def _tick(self) -> None:
        now = time.monotonic()
        if now - self._last_emit < self._interval:
            return
        self._last_emit = now
        self._tracker.record(self._downloaded, now)
        self._emit("progress", self.progress)


def _total_from(response: Any, offset: int) -> Optional[int]:
    match = re.search(r"/(\d+)$", response.headers.get("content-range", ""))
    if match:
        return int(match.group(1))
    try:
        length = int(response.headers.get("content-length", ""))
    except ValueError:
        return None
    return offset + length if length > 0 else None
