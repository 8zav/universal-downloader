from __future__ import annotations

import json
import re
from functools import partial
from typing import Any, Dict, List, Optional

from ..errors import HttpError, ProviderError
from ..htmlutil import decode_html_entities
from ..http import DEFAULT_USER_AGENT, Response
from ..session import Session
from ..source import Context, Located, Source

_LINK = re.compile(r"swisstransfer\.com/(?:[a-z]{2}/)?(?:d|dl)/([a-f0-9-]{36})", re.IGNORECASE)
_BASE = "https://www.swisstransfer.com"
_SCRIPT = re.compile(r"<script\b[^>]*\bdata-page=[\"'][^\"']*[\"'][^>]*>([\s\S]*?)</script>", re.IGNORECASE)
_ATTRIBUTE = re.compile(r"\bdata-page=\"(\{[^\"]*)\"")


def _find_file_list(node: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(node, list):
        looks_like_files = bool(node) and all(
            isinstance(item, dict) and item.get("id") and (item.get("path") or item.get("name")) for item in node
        )
        if looks_like_files:
            return node
        children = node
    elif isinstance(node, dict):
        children = list(node.values())
    else:
        return None
    for child in children:
        found = _find_file_list(child)
        if found:
            return found
    return None


def _read_page(response: Response) -> Dict[str, Any]:
    body = response.text()
    if body.lstrip().startswith("{"):
        return json.loads(body)
    script = _SCRIPT.search(body)
    attribute = _ATTRIBUTE.search(body)
    payload = (script.group(1).strip() if script else "") or (
        decode_html_entities(attribute.group(1)) if attribute else ""
    )
    if not payload:
        raise ProviderError("Page data not found (SwissTransfer layout changed?)", provider="swisstransfer")
    try:
        return json.loads(payload)
    except ValueError:
        raise ProviderError("Page data is not valid JSON (SwissTransfer layout changed?)",
                            provider="swisstransfer") from None


def _signed_download_url(session: Session, link_id: str, file_id: str, _client: Any) -> Located:
    answer = session.json(f"{_BASE}/api/1/links/{link_id}/files/{file_id}",
                          headers={"Accept": "application/json", "Referer": f"{_BASE}/dl/{link_id}"})
    url = (answer.get("data") or {}).get("url") if isinstance(answer, dict) else None
    if not isinstance(answer, dict) or answer.get("result") != "success" or not url:
        raise ProviderError("SwissTransfer did not return a download URL", provider="swisstransfer")
    return Located(url=url, headers={})


class SwissTransfer:
    name = "swisstransfer"

    def matches(self, url: str) -> bool:
        return bool(_LINK.search(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        link_id = _LINK.search(url).group(1)
        session = Session(context.client, {"User-Agent": DEFAULT_USER_AGENT})

        try:
            page = _read_page(session.send(f"{_BASE}/dl/{link_id}",
                                           headers={"Accept": "text/html,application/xhtml+xml", "Referer": f"{_BASE}/"}))
        except HttpError as error:
            if error.status == 404:
                raise ProviderError("Transfer not found (expired or deleted)", provider=self.name) from None
            raise

        component = str(page.get("component", ""))
        if re.search("password", component, re.IGNORECASE):
            raise ProviderError("This transfer is password protected (not supported yet)", provider=self.name)
        files = _find_file_list(page.get("props"))
        if not files:
            raise ProviderError(f"No files in the page ({component or 'unknown page'})", provider=self.name)

        session.set_cookie("SWISSTRANSFER-CGU-ACCEPTED", "true")
        sources = []
        for file in files:
            segments = [part for part in str(file.get("path") or file.get("name")).split("/") if part]
            size = file.get("size")
            sources.append(Source(
                id=str(file["id"]),
                url=f"{_BASE}/api/1/links/{link_id}/files/{file['id']}",
                filename=segments.pop() if segments else None,
                subdir="/".join(segments) or None,
                size=int(size) if str(size).isdigit() and int(size) > 0 else None,
                resolve_url=partial(_signed_download_url, session, link_id, str(file["id"])),
            ))
        return sources


swisstransfer = SwissTransfer()
