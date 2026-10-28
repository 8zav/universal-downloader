from __future__ import annotations

import re
from typing import List
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..http import file_name_from_url
from ..source import Context, Source

_LINK = re.compile(r"^https?://(?:www\.)?dropbox\.com/(?:s|scl|sh)/", re.IGNORECASE)


class Dropbox:
    name = "dropbox"

    def matches(self, url: str) -> bool:
        return bool(_LINK.match(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        parts = urlparse(url)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if key not in ("raw", "dl")]
        query.append(("dl", "1"))
        link = urlunparse(parts._replace(query=urlencode(query)))
        is_folder = parts.path.startswith("/scl/fo/") or parts.path.startswith("/sh/")
        filename = None if is_folder else file_name_from_url(link)
        return [Source(id=link, url=link, filename=filename)]


dropbox = Dropbox()
