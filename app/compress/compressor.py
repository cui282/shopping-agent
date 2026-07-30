from __future__ import annotations

import json
import os
from typing import Any

from app.compress.breakpoint import CacheBreakpoint


def compress_messages(messages: list[dict[str, Any]]) -> CacheBreakpoint:
    keep_recent = max(1, int(os.getenv("COMPRESS_KEEP_RECENT", "3")))
    if len(messages) <= keep_recent:
        return CacheBreakpoint(recent=messages)
    older = messages[:-keep_recent]
    summary_parts = []
    for message in older:
        role = message.get("role", "event")
        content = json.dumps(message.get("content", message), ensure_ascii=False)
        summary_parts.append(f"{role}: {content[:240]}")
    return CacheBreakpoint(
        summary="\n".join(summary_parts),
        recent=messages[-keep_recent:],
        compressed_count=len(older),
    )
