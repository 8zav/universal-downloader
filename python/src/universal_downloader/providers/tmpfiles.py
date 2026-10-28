from __future__ import annotations

import re
from typing import List

from ..http import file_name_from_url
from ..source import Context, Source

_LANDING = re.compile(r"^https?://(?:www\.)?tmpfiles\.org/(?!dl/)(\d+)/(.+)$", re.IGNORECASE)
_DIRECT = re.compile(r"^https?://(?:www\.)?tmpfiles\.org/dl/", re.IGNORECASE)


class Tmpfiles:
    name = "tmpfiles"

    def matches(self, url: str) -> bool:
        return bool(_LANDING.match(url) or _DIRECT.match(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        landing = _LANDING.match(url)
        target = f"https://tmpfiles.org/dl/{landing.group(1)}/{landing.group(2)}" if landing else url
        return [Source(id=target, url=target, filename=file_name_from_url(target))]


tmpfiles = Tmpfiles()
