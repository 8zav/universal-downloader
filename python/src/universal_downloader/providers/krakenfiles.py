from __future__ import annotations

import re
import uuid
from typing import List

from ..errors import ProviderError
from ..htmlutil import find_tag, read_attribute
from ..http import file_name_from_url
from ..session import Session
from ..source import Context, Source

_LINK = re.compile(r"^https?://(?:www\.)?krakenfiles\.com/view/([\w-]+)/", re.IGNORECASE)
_BASE = "https://krakenfiles.com"


class Krakenfiles:
    name = "krakenfiles"

    def matches(self, url: str) -> bool:
        return bool(_LINK.match(url))

    def resolve(self, url: str, context: Context) -> List[Source]:
        session = Session(context.client)
        page = session.text(url)

        token = read_attribute(find_tag(page, r"<input\b[^>]*id=[\"']dl-token[\"'][^>]*>") or "", "value")
        file_hash = re.search(r"data-file-hash=[\"']([^\"']+)[\"']", page, re.IGNORECASE)
        if not token or not file_hash:
            raise ProviderError("Token or file hash not found (file removed or page layout changed)",
                                provider=self.name)

        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="token"\r\n\r\n{token}\r\n'
                f"--{boundary}--\r\n").encode()
        result = session.json(
            f"{_BASE}/download/{file_hash.group(1)}", method="POST", data=body,
            headers={"hash": file_hash.group(1), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        final_url = result.get("url") if isinstance(result, dict) else None
        if not final_url:
            raise ProviderError("The host refused to give a download URL", provider=self.name)

        cookie = session.cookie_header()
        headers = {"Referer": url, **({"Cookie": cookie} if cookie else {})}
        return [Source(id=file_hash.group(1), url=final_url, filename=file_name_from_url(final_url), headers=headers)]


krakenfiles = Krakenfiles()
