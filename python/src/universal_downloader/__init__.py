from __future__ import annotations

from typing import Any, List, Sequence, Union

from .download import Download, Progress
from .errors import CancelledError, HttpError, ProviderError, UdlError
from .format import format_bytes, format_duration, format_speed
from .http import HttpClient
from .manager import DownloadManager, Stats
from .providers import BUILTIN_PROVIDERS, find_provider, resolve_sources
from .source import Context, Located, Source

__version__ = "0.1.0"


def download(urls: Union[str, Sequence[str]], **options: Any) -> List[str]:
    manager = DownloadManager(**options)
    batches = [manager.add(url) for url in ([urls] if isinstance(urls, str) else urls)]
    return [item.wait() for batch in batches for item in batch]


__all__ = [
    "BUILTIN_PROVIDERS",
    "CancelledError",
    "Context",
    "Download",
    "DownloadManager",
    "HttpClient",
    "HttpError",
    "Located",
    "Progress",
    "ProviderError",
    "Source",
    "Stats",
    "UdlError",
    "__version__",
    "download",
    "find_provider",
    "format_bytes",
    "format_duration",
    "format_speed",
    "resolve_sources",
]
