from __future__ import annotations

import math
from typing import Optional

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(size: Optional[float]) -> str:
    if size is None or not math.isfinite(size) or size < 0:
        return "?"
    value = float(size)
    unit = 0
    while value >= 1024 and unit < len(_BYTE_UNITS) - 1:
        value /= 1024
        unit += 1
    return f"{value:.0f} B" if unit == 0 else f"{value:.2f} {_BYTE_UNITS[unit]}"


def format_speed(bytes_per_second: float) -> str:
    return f"{format_bytes(bytes_per_second)}/s"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "?"
    total = round(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
