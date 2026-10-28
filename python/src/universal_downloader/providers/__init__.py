from __future__ import annotations

from typing import Any, List, Sequence, Tuple

from ..errors import ProviderError
from ..source import Context, Source
from .direct import direct
from .dropbox import dropbox
from .gofile import gofile
from .googledrive import googledrive
from .krakenfiles import krakenfiles
from .mediafire import mediafire
from .swisstransfer import swisstransfer
from .tmpfiles import tmpfiles
from .wetransfer import wetransfer

BUILTIN_PROVIDERS = (
    gofile,
    swisstransfer,
    mediafire,
    googledrive,
    dropbox,
    wetransfer,
    krakenfiles,
    tmpfiles,
    direct,
)


def find_provider(url: str, extra_providers: Sequence[Any] = ()) -> Any:
    for provider in (*extra_providers, *BUILTIN_PROVIDERS):
        if provider.matches(url):
            return provider
    raise ProviderError(f"Unsupported URL: {url}")


def resolve_sources(url: str, context: Context, providers: Sequence[Any] = ()) -> Tuple[Any, List[Source]]:
    provider = find_provider(url, providers)
    return provider, provider.resolve(url, context)


__all__ = [
    "BUILTIN_PROVIDERS",
    "direct",
    "dropbox",
    "find_provider",
    "gofile",
    "googledrive",
    "krakenfiles",
    "mediafire",
    "resolve_sources",
    "swisstransfer",
    "tmpfiles",
    "wetransfer",
]
