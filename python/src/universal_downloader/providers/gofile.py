from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Dict, List, Optional

from ..errors import ProviderError
from ..http import DEFAULT_USER_AGENT, get_json, post_json
from ..source import Context, Source

_LINK = re.compile(r"gofile\.io/d/([A-Za-z0-9]+)", re.IGNORECASE)
_API = "https://api.gofile.io"
_TOKEN_TTL_SECONDS = 2 * 60 * 60
_WEBSITE_TOKEN_SECRET = "5d4f7g8sd45fsd"
_WEBSITE_TOKEN_SLOT_SECONDS = 4 * 60 * 60
_WEBSITE_LANGUAGE = "en-US"

_token_lock = threading.Lock()
_cached_token: Dict[str, Any] = {"value": None, "created_at": 0.0}


def build_website_token(account_token: str, user_agent: str = DEFAULT_USER_AGENT, now: Optional[float] = None) -> str:
    moment = time.time() if now is None else now
    slot = int(moment // _WEBSITE_TOKEN_SLOT_SECONDS)
    raw = f"{user_agent}::{_WEBSITE_LANGUAGE}::{account_token}::{slot}::{_WEBSITE_TOKEN_SECRET}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _guest_token(client: Any) -> str:
    with _token_lock:
        if _cached_token["value"] and time.time() - _cached_token["created_at"] < _TOKEN_TTL_SECONDS:
            return _cached_token["value"]
        response = post_json(client, f"{_API}/accounts", headers={"Referer": "https://gofile.io/"})
        value = (response.get("data") or {}).get("token")
        if response.get("status") != "ok" or not value:
            raise ProviderError(f"Could not create a guest account ({response.get('status', 'unknown')})",
                                provider="gofile")
        _cached_token.update(value=value, created_at=time.time())
        return value


def _list_folder(client: Any, folder_id: str, token: str, password: Optional[str], subdir: str,
                 files: List[Source]) -> None:
    query = f"wt={build_website_token(token)}"
    if password:
        query += f"&password={hashlib.sha256(password.encode()).hexdigest()}"
    response = get_json(client, f"{_API}/contents/{folder_id}?{query}", headers={
        "Authorization": f"Bearer {token}",
        "X-Website-Token": build_website_token(token),
        "Referer": "https://gofile.io/",
    })
    if response.get("status") != "ok":
        raise ProviderError(f"Folder unavailable: {response.get('status', 'unknown error')}", provider="gofile")

    for child in (response.get("data") or {}).get("children", {}).values():
        if child.get("type") == "folder":
            child_dir = "/".join(part for part in (subdir, child.get("name")) if part)
            _list_folder(client, child["id"], token, password, child_dir, files)
        elif child.get("link"):
            size = child.get("size")
            files.append(Source(
                id=child["id"],
                url=child["link"],
                filename=child.get("name"),
                subdir=subdir or None,
                size=int(size) if str(size).isdigit() and int(size) > 0 else None,
                headers={"Cookie": f"accountToken={token}", "Authorization": f"Bearer {token}",
                         "Referer": "https://gofile.io/"},
            ))


class GoFile:
    name = "gofile"

    def matches(self, url: str) -> bool:
        return bool(_LINK.search(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        folder_id = _LINK.search(url).group(1)
        token = _guest_token(context.client)
        files: List[Source] = []
        _list_folder(context.client, folder_id, token, context.password, "", files)
        if not files:
            raise ProviderError("No files found in this GoFile link", provider=self.name)
        return files


gofile = GoFile()
