from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterator, List, Mapping, Optional
from urllib.parse import unquote, urlparse

from .errors import HttpError

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 30.0
CHUNK_SIZE = 64 * 1024


class StreamResponse:
    def __init__(self, raw: Any):
        self._raw = raw
        self.status: int = raw.status
        self.url: str = raw.geturl()
        self.headers: Dict[str, str] = {key.lower(): value for key, value in raw.headers.items()}
        self.set_cookies: List[str] = list(raw.headers.get_all("Set-Cookie") or [])

    def read(self, size: int = -1) -> bytes:
        return self._raw.read(size)

    def iter_chunks(self, size: int = CHUNK_SIZE) -> Iterator[bytes]:
        while True:
            chunk = self._raw.read(size)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        self._interrupt_socket()
        self._raw.close()

    def _interrupt_socket(self) -> None:
        try:
            self._raw.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            return

    def __enter__(self) -> "StreamResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class Response:
    def __init__(self, status: int = 200, body: bytes = b"", headers: Optional[Mapping[str, str]] = None,
                 url: str = "", set_cookies: Optional[List[str]] = None):
        self.status = status
        self.body = body
        self.headers: Dict[str, str] = {key.lower(): value for key, value in (headers or {}).items()}
        self.url = url
        self.set_cookies = list(set_cookies or [])
        self._cursor = 0

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)

    def read(self, size: int = -1) -> bytes:
        end = len(self.body) if size < 0 else self._cursor + size
        chunk = self.body[self._cursor:end]
        self._cursor += len(chunk)
        return chunk

    def iter_chunks(self, size: int = CHUNK_SIZE) -> Iterator[bytes]:
        while True:
            chunk = self.read(size)
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        return None

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class HttpClient:
    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: float = REQUEST_TIMEOUT_SECONDS):
        self._user_agent = user_agent
        self._timeout = timeout

    def open(self, url: str, *, method: str = "GET", headers: Optional[Mapping[str, str]] = None,
             data: Optional[bytes] = None) -> StreamResponse:
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {url}")
        request = urllib.request.Request(url, data=data, method=method,
                                         headers={"User-Agent": self._user_agent, **(headers or {})})
        try:
            return StreamResponse(urllib.request.urlopen(request, timeout=self._timeout))
        except urllib.error.HTTPError as error:
            retry_after = _parse_retry_after(error.headers.get("Retry-After") if error.headers else None)
            error.close()
            raise HttpError(error.code, str(error.reason), url, retry_after) from None

    def request(self, url: str, *, method: str = "GET", headers: Optional[Mapping[str, str]] = None,
                data: Optional[bytes] = None) -> Response:
        with self.open(url, method=method, headers=headers, data=data) as stream:
            body = stream.read()
            return Response(stream.status, body, stream.headers, stream.url, stream.set_cookies)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def parse_content_disposition(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    encoded = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", header)
    if encoded:
        return unquote(encoded.group(1).strip())
    plain = re.search(r'filename\s*=\s*"?([^";]+)"?', header)
    return plain.group(1).strip() if plain else None


def file_name_from_url(url: str) -> Optional[str]:
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    return unquote(segments[-1]) if segments else None


def get_text(client: Any, url: str, headers: Optional[Mapping[str, str]] = None) -> str:
    return client.request(url, headers=headers).text()


def get_json(client: Any, url: str, headers: Optional[Mapping[str, str]] = None) -> Any:
    return client.request(url, headers=headers).json()


def post_json(client: Any, url: str, headers: Optional[Mapping[str, str]] = None) -> Any:
    return client.request(url, method="POST", headers=headers).json()
