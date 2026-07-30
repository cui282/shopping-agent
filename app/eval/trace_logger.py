from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def log_trace(thread_id: str, payload: dict[str, Any]) -> Path:
    root = Path(os.getenv("TRACE_LOG_DIR", "./data/traces")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{thread_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path
