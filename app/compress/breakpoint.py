from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CacheBreakpoint:
    summary: str = ""
    recent: list[dict[str, Any]] = field(default_factory=list)
    compressed_count: int = 0
