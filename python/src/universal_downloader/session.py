from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .http import HttpClient, Response, StreamResponse


class Session:
    def __init__(self, client: Optional[Any] = None, base_headers: Optional[Mapping[str, str]] = None):
        self._client = client or HttpClient()
        self._base_headers = dict(base_headers or {})
        self._cookies: Dict[str, str] = {}

    def cookie_header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self._cookies.items())

    def set_cookie(self, name: str, value: str) -> None:
        self._cookies[name] = value

    def open(self, url: str, *, method: str = "GET", headers: Optional[Mapping[str, str]] = None,
             data: Optional[bytes] = None) -> StreamResponse:
        merged = {**self._base_headers, **(headers or {})}
        if self._cookies:
            merged["Cookie"] = self.cookie_header()
        response = self._client.open(url, method=method, headers=merged, data=data)
        self._remember(response.set_cookies)
        return response

    def send(self, url: str, *, method: str = "GET", headers: Optional[Mapping[str, str]] = None,
             data: Optional[bytes] = None) -> Response:
        with self.open(url, method=method, headers=headers, data=data) as stream:
            return Response(stream.status, stream.read(), stream.headers, stream.url, stream.set_cookies)

    def text(self, url: str, **options: Any) -> str:
        return self.send(url, **options).text()

    def json(self, url: str, **options: Any) -> Any:
        return self.send(url, **options).json()

    def _remember(self, set_cookie_lines: list) -> None:
        for line in set_cookie_lines:
            pair = line.split(";", 1)[0]
            name, separator, value = pair.partition("=")
            if separator and name.strip():
                self._cookies[name.strip()] = value.strip()
