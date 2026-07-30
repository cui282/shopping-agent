from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=1)
def load_prompts() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / "prompt" / "prompts.yml"
    values = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in values.items()}
