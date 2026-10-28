from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..errors import ProviderError
from ..session import Session
from ..source import Context, Source

_HOST = re.compile(r"^https?://(?:we\.tl|(?:www\.)?wetransfer\.com)/", re.IGNORECASE)
_SHORT = re.compile(r"^https?://we\.tl/", re.IGNORECASE)
_API = "https://wetransfer.com/api/v4/transfers"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:102.0) Gecko/20100101 Firefox/102.0"


def _parse_path(url: str) -> Optional[Tuple[str, Optional[str], str]]:
    parts = [part for part in urlparse(url).path.split("/") if part]
    rest = parts[1:] if parts and parts[0] == "downloads" else parts
    if len(rest) == 2:
        return rest[0], None, rest[1]
    if len(rest) == 3:
        return rest[0], rest[1], rest[2]
    return None


class WeTransfer:
    name = "wetransfer"

    def matches(self, url: str) -> bool:
        return bool(_HOST.match(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        session = Session(context.client, {"User-Agent": _USER_AGENT, "x-requested-with": "XMLHttpRequest"})

        target = session.send(url, method="HEAD").url if _SHORT.match(url) else url
        ids = _parse_path(target)
        if ids is None:
            raise ProviderError("Unrecognized WeTransfer link", provider=self.name)
        transfer_id, recipient_id, security_hash = ids

        home = session.text("https://wetransfer.com/")
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', home)

        body: Dict[str, str] = {"intent": "entire_transfer", "security_hash": security_hash}
        if recipient_id:
            body["recipient_id"] = recipient_id
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["x-csrf-token"] = csrf.group(1)

        result = session.json(f"{_API}/{transfer_id}/download", method="POST", headers=headers,
                              data=json.dumps(body).encode())
        direct_link = result.get("direct_link") if isinstance(result, dict) else None
        if not direct_link:
            message = result.get("message") if isinstance(result, dict) else None
            raise ProviderError(message or "No download link (transfer expired or deleted?)", provider=self.name)
        return [Source(id=transfer_id, url=direct_link, headers={"User-Agent": _USER_AGENT})]


wetransfer = WeTransfer()
