from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlencode

from ..errors import ProviderError
from ..htmlutil import decode_html_entities, find_tag, read_attribute
from ..http import parse_content_disposition
from ..session import Session
from ..source import Context, Source

_FILE_ID_PATTERNS = (
    re.compile(r"drive\.google\.com/file/d/([\w-]+)", re.IGNORECASE),
    re.compile(r"[?&]id=([\w-]+)", re.IGNORECASE),
)
_HOST = re.compile(r"^https?://(?:drive|docs)\.google\.com/", re.IGNORECASE)
_DOWNLOAD_URL = "https://drive.usercontent.google.com/download"


def _extract_file_id(url: str) -> Optional[str]:
    for pattern in _FILE_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def _parse_confirm_form(page: str) -> Optional[str]:
    form = find_tag(page, r"<form\b[^>]*id=[\"']download-form[\"'][^>]*>")
    if not form:
        return None
    params = []
    for tag in re.finditer(r"<input\b[^>]*type=[\"']hidden[\"'][^>]*>", page, re.IGNORECASE):
        name = read_attribute(tag.group(0), "name")
        if name:
            params.append((name, decode_html_entities(read_attribute(tag.group(0), "value") or "")))
    action = decode_html_entities(read_attribute(form, "action") or _DOWNLOAD_URL)
    return f"{action}?{urlencode(params)}"


class GoogleDrive:
    name = "googledrive"

    def matches(self, url: str) -> bool:
        return bool(_HOST.match(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        if re.search(r"/drive/folders/", url, re.IGNORECASE):
            raise ProviderError(
                "Google Drive folders need an API key and are not supported, use single file links",
                provider=self.name,
            )
        file_id = _extract_file_id(url)
        if not file_id:
            raise ProviderError("Could not find the file id in this link", provider=self.name)

        session = Session(context.client)
        first_url = f"{_DOWNLOAD_URL}?{urlencode({'id': file_id, 'export': 'download', 'confirm': 't'})}"
        final_url = first_url
        response = session.open(first_url)
        try:
            if "text/html" in response.headers.get("content-type", ""):
                page = response.read().decode("utf-8", errors="replace")
                response.close()
                confirm_url = _parse_confirm_form(page)
                if not confirm_url:
                    raise ProviderError(
                        "File not shared publicly, download quota exceeded, or removed", provider=self.name
                    )
                final_url = confirm_url
                response = session.open(final_url)
            length = response.headers.get("content-length", "")
            size = int(length) if length.isdigit() and int(length) > 0 else None
            filename = parse_content_disposition(response.headers.get("content-disposition"))
        finally:
            response.close()

        cookie = session.cookie_header()
        return [Source(id=file_id, url=final_url, filename=filename, size=size,
                       headers={"Cookie": cookie} if cookie else {})]


googledrive = GoogleDrive()
