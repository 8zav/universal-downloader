from __future__ import annotations

import html
import re
from typing import Optional, Pattern, Union


def read_attribute(tag: str, name: str) -> Optional[str]:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
    return match.group(1) if match else None


def find_tag(source: str, pattern: Union[str, Pattern[str]]) -> Optional[str]:
    compiled = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
    match = compiled.search(source)
    return match.group(0) if match else None


def decode_html_entities(text: str) -> str:
    return html.unescape(text)
