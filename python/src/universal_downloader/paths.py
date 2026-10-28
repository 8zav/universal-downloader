from __future__ import annotations

import os
import re
from typing import Optional

_MAX_NAME_LENGTH = 200
_FALLBACK_NAME = "download"
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_file_name(name: Optional[str], fallback: str = _FALLBACK_NAME) -> str:
    cleaned = _FORBIDDEN.sub("_", str(name or "")).lstrip(".").strip()[:_MAX_NAME_LENGTH]
    return cleaned or fallback


def safe_relative_dir(directory: Optional[str]) -> str:
    if not directory:
        return ""
    parts = [part for part in re.split(r"[\\/]+", str(directory)) if part and part not in (".", "..")]
    return os.sep.join(safe_file_name(part, "_") for part in parts)
