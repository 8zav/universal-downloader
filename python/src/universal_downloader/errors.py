from __future__ import annotations

from typing import Optional


class UdlError(Exception):
    pass


class HttpError(UdlError):
    def __init__(self, status: int, reason: str = "", url: str = "", retry_after: Optional[float] = None):
        suffix = f" ({url})" if url else ""
        super().__init__(f"HTTP {status}{' ' + reason if reason else ''}{suffix}")
        self.status = status
        self.retry_after = retry_after


class ProviderError(UdlError):
    def __init__(self, message: str, provider: Optional[str] = None):
        super().__init__(f"[{provider}] {message}" if provider else message)
        self.provider = provider


class CancelledError(UdlError):
    def __init__(self, message: str = "Download cancelled"):
        super().__init__(message)


def is_retryable(error: BaseException) -> bool:
    if isinstance(error, HttpError):
        return error.status in (408, 429) or error.status >= 500
    if isinstance(error, (ProviderError, CancelledError)):
        return False
    return True
