from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, DefaultDict, Dict, List, Optional, Sequence

from .download import Download
from .format import format_duration
from .http import HttpClient
from .providers import resolve_sources
from .source import Context, Source

_DEFAULT_CONCURRENCY = 3
_RELEASE_STATES = frozenset({"paused", "completed", "failed", "cancelled"})
_FINISHED_STATES = frozenset({"completed", "failed", "cancelled"})

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stats:
    active: int
    queued: int
    paused: int
    completed: int
    failed: int
    downloaded: int
    remaining: int
    speed: float
    eta: Optional[float]
    eta_text: str


class DownloadManager:
    def __init__(self, directory: str = ".", concurrency: int = _DEFAULT_CONCURRENCY,
                 providers: Sequence[Any] = (), client: Optional[Any] = None, **download_defaults: Any):
        self._concurrency = max(1, int(concurrency))
        self._providers = list(providers)
        self._client = client or HttpClient()
        self._defaults: Dict[str, Any] = {"directory": directory, **download_defaults}
        self._downloads: List[Download] = []
        self._pending: List[Download] = []
        self._active: set = set()
        self._cond = threading.Condition()
        self._listeners: DefaultDict[str, List[Callable[..., None]]] = defaultdict(list)

    @property
    def downloads(self) -> List[Download]:
        return list(self._downloads)

    def on(self, event: str, callback: Callable[..., None]) -> "DownloadManager":
        self._listeners[event].append(callback)
        return self

    def add(self, url: str, **overrides: Any) -> List[Download]:
        options = {**self._defaults, **overrides}
        password = options.pop("password", None)
        filename = options.pop("filename", None)
        context = Context(self._client, password)
        provider, sources = resolve_sources(url, context, self._providers)

        def resolve_again() -> List[Source]:
            return provider.resolve(url, context)

        single_name = filename if len(sources) == 1 else None
        return [self._enqueue(source, resolve_again, filename=single_name, **options) for source in sources]

    def pause(self, download: Download) -> bool:
        return download.pause()

    def resume(self, download: Download) -> bool:
        with self._cond:
            if download.state != "paused" or download in self._pending:
                return False
            self._pending.insert(0, download)
        self._pump()
        return True

    def pause_all(self) -> None:
        for download in self.downloads:
            download.pause()

    def resume_all(self) -> None:
        for download in self.downloads:
            if download.state == "paused":
                self.resume(download)

    def cancel_all(self, delete_file: bool = True) -> None:
        for download in self.downloads:
            download.cancel(delete_file)

    def stats(self) -> Stats:
        snapshots = [download.progress for download in self.downloads]

        def count(state: str) -> int:
            return sum(1 for snapshot in snapshots if snapshot.state == state)

        unfinished = [snapshot for snapshot in snapshots if snapshot.state not in _FINISHED_STATES]
        speed = sum(snapshot.speed for snapshot in snapshots)
        remaining = sum(snapshot.remaining or 0 for snapshot in unfinished)
        eta = remaining / speed if speed > 0 else None
        return Stats(
            active=count("downloading"), queued=count("queued"), paused=count("paused"),
            completed=count("completed"), failed=count("failed"),
            downloaded=sum(snapshot.downloaded for snapshot in snapshots),
            remaining=remaining, speed=speed, eta=eta, eta_text=format_duration(eta),
        )

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        with self._cond:
            return self._cond.wait_for(self._is_idle, timeout)


    def _emit(self, event: str, *args: Any) -> None:
        for callback in list(self._listeners[event]):
            try:
                callback(*args)
            except Exception:
                log.exception("Listener for %r raised", event)

    def _enqueue(self, source: Source, resolve_again: Callable[[], List[Source]], **options: Any) -> Download:
        download = Download(source, client=self._client, resolve_again=resolve_again, **options)
        download.on("state", lambda state, item=download: self._on_state(item, state))
        download.on("progress", lambda snapshot, item=download: self._emit("progress", item, snapshot))
        download.on("completed", lambda path, item=download: self._emit("completed", item, path))
        download.on("failed", lambda error, item=download: self._emit("failed", item, error))
        with self._cond:
            self._downloads.append(download)
            self._pending.append(download)
        self._emit("added", download)
        self._pump()
        return download

    def _on_state(self, download: Download, state: str) -> None:
        self._emit("state", download, state)
        if state not in _RELEASE_STATES:
            return
        with self._cond:
            self._active.discard(download)
            if download in self._pending:
                self._pending.remove(download)
            self._cond.notify_all()
        self._pump()

    def _pump(self) -> None:
        to_run: List[Download] = []
        with self._cond:
            while len(self._active) < self._concurrency and self._pending:
                download = self._pending.pop(0)
                if download.state in _FINISHED_STATES:
                    continue
                self._active.add(download)
                to_run.append(download)
        for download in to_run:
            if download.state == "paused":
                download.resume()
            else:
                download.start()
        with self._cond:
            idle = self._is_idle()
            self._cond.notify_all()
        if idle:
            self._emit("idle")

    def _is_idle(self) -> bool:
        return not self._active and not self._pending
