from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from universal_downloader.http import Response

CHUNK_SIZE = 16 * 1024


def wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("wait_for timed out")
        time.sleep(0.01)


class RangeServer:
    def __init__(self, content: bytes, delay: float = 0.0, honor_range: bool = True):
        self.requests: List[Dict[str, Optional[str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return None

            def do_GET(self) -> None:
                header = self.headers.get("Range")
                outer.requests.append({"url": self.path, "range": header})
                match = re.match(r"bytes=(\d+)-", header or "") if honor_range else None
                start = int(match.group(1)) if match else 0
                if start >= len(content):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(content)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = content[start:]
                self.send_response(206 if match else 200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/octet-stream")
                if match:
                    self.send_header("Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}")
                self.end_headers()
                try:
                    for offset in range(0, len(body), CHUNK_SIZE):
                        self.wfile.write(body[offset:offset + CHUNK_SIZE])
                        self.wfile.flush()
                        if delay:
                            time.sleep(delay)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.port = self._server.server_address[1]

    def url(self, name: str = "file.bin") -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


Handler = Callable[[str, str, Dict[str, str], Optional[bytes]], Response]


class FakeClient:
    def __init__(self, routes: List[Tuple[str, Handler]]):
        self.routes = routes
        self.calls: List[Dict[str, Any]] = []

    def open(self, url: str, *, method: str = "GET", headers: Optional[Dict[str, str]] = None,
             data: Optional[bytes] = None) -> Response:
        self.calls.append({"url": url, "method": method, "headers": dict(headers or {}), "data": data})
        for pattern, handler in self.routes:
            if re.search(pattern, url):
                return handler(url, method, dict(headers or {}), data)
        raise AssertionError(f"unexpected request: {url}")

    def request(self, url: str, **options: Any) -> Response:
        return self.open(url, **options)


def json_response(body: Any, status: int = 200, **extra: Any) -> Response:
    return Response(status, json.dumps(body).encode(), {"content-type": "application/json"}, **extra)


def html_response(body: str, **extra: Any) -> Response:
    return Response(200, body.encode(), {"content-type": "text/html"}, **extra)
