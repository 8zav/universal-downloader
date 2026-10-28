from __future__ import annotations

import base64
import binascii
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from ..errors import ProviderError
from ..htmlutil import decode_html_entities, find_tag, read_attribute
from ..http import file_name_from_url, get_json, get_text
from ..source import Context, Source

_HOST = re.compile(r"^https?://(?:[\w-]+\.)?mediafire\.com/", re.IGNORECASE)
_DIRECT_HOST = re.compile(r"^https?://download\d*\.mediafire\.com/", re.IGNORECASE)
_FOLDER = re.compile(r"mediafire\.com/folder/([A-Za-z0-9]+)", re.IGNORECASE)
_BUTTON = re.compile(
    r"<a\b[^>]*(?:id\s*=\s*[\"']downloadButton[\"']|class\s*=\s*[\"'][^\"']*\bpopsok\b[^\"']*[\"'])[^>]*>",
    re.IGNORECASE,
)
_FOLDER_API = "https://www.mediafire.com/api/1.5/folder/get_content.php"
_FOLDER_CHUNK_SIZE = 100
_MAX_FOLDER_DEPTH = 8
_PAGE_RESOLVE_WORKERS = 4


def extract_download_link(page: str) -> Optional[str]:
    button = find_tag(page, _BUTTON)
    if not button:
        return None
    scrambled = read_attribute(button, "data-scrambled-url")
    if scrambled:
        try:
            decoded = base64.b64decode(scrambled).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            decoded = ""
        if re.match(r"^https?://", decoded, re.IGNORECASE):
            return decoded
    href = read_attribute(button, "href")
    return decode_html_entities(href) if href and re.match(r"^https?://", href, re.IGNORECASE) else None


def _resolve_file_page(client: Any, page_url: str) -> Source:
    link = extract_download_link(get_text(client, page_url))
    if not link:
        raise ProviderError("Download button not found (file removed or page layout changed)", provider="mediafire")
    return Source(id=link, url=link, filename=file_name_from_url(link), headers={"Referer": page_url})


def _fetch_folder_page(client: Any, folder_key: str, content_type: str, chunk: int) -> Dict[str, Any]:
    query = urlencode({
        "folder_key": folder_key,
        "content_type": content_type,
        "chunk": chunk,
        "chunk_size": _FOLDER_CHUNK_SIZE,
        "response_format": "json",
    })
    response = get_json(client, f"{_FOLDER_API}?{query}").get("response", {})
    if response.get("result") != "Success":
        raise ProviderError(response.get("message") or "Folder unavailable", provider="mediafire")
    return response.get("folder_content", {})


def _list_folder(client: Any, folder_key: str, subdir: str = "", depth: int = 0,
                 found: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    entries = [] if found is None else found

    chunk, more = 1, True
    while more:
        content = _fetch_folder_page(client, folder_key, "files", chunk)
        for file in content.get("files", []):
            size = file.get("size")
            entries.append({
                "page_url": (file.get("links") or {}).get("normal_download"),
                "name": file.get("filename"),
                "size": int(size) if str(size).isdigit() and int(size) > 0 else None,
                "subdir": subdir,
            })
        more = content.get("more_chunks") == "yes"
        chunk += 1

    if depth < _MAX_FOLDER_DEPTH:
        chunk, more = 1, True
        while more:
            content = _fetch_folder_page(client, folder_key, "folders", chunk)
            for folder in content.get("folders", []):
                child_dir = "/".join(part for part in (subdir, folder.get("name")) if part)
                _list_folder(client, folder["folderkey"], child_dir, depth + 1, entries)
            more = content.get("more_chunks") == "yes"
            chunk += 1
    return entries


class MediaFire:
    name = "mediafire"

    def matches(self, url: str) -> bool:
        return bool(_HOST.match(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        if _DIRECT_HOST.match(url):
            return [Source(id=url, url=url, filename=file_name_from_url(url),
                           headers={"Referer": "https://www.mediafire.com/"})]

        folder = _FOLDER.search(url)
        if not folder:
            return [_resolve_file_page(context.client, url)]

        entries = [entry for entry in _list_folder(context.client, folder.group(1)) if entry["page_url"]]
        if not entries:
            raise ProviderError("The folder is empty", provider=self.name)

        def resolve_entry(entry: Dict[str, Any]) -> Source:
            source = _resolve_file_page(context.client, entry["page_url"])
            return replace(source, filename=entry["name"], size=entry["size"], subdir=entry["subdir"] or None)

        with ThreadPoolExecutor(max_workers=_PAGE_RESOLVE_WORKERS) as pool:
            return list(pool.map(resolve_entry, entries))


mediafire = MediaFire()
