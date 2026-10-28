from __future__ import annotations

import re
from typing import List

from ..http import file_name_from_url
from ..source import Context, Source


class Direct:
    name = "direct"

    def matches(self, url: str) -> bool:
        return bool(re.match(r"^https?://", url, re.IGNORECASE))

    def resolve(self, url: str, context: Context) -> List[Source]:
        return [Source(id=url, url=url, filename=file_name_from_url(url))]


direct = Direct()
