from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class Located:
    url: str
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    filename: Optional[str] = None
    subdir: Optional[str] = None
    size: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    resolve_url: Optional[Callable[[Any], Located]] = None


@dataclass(frozen=True)
class Context:
    client: Any
    password: Optional[str] = None
