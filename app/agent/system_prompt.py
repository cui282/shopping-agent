from __future__ import annotations

import json
from typing import Any

from app.agent.prompts import load_prompts


def build_system_prompt(preferences: dict[str, Any] | None = None) -> str:
    rendered = json.dumps(preferences or {}, ensure_ascii=False)
    return load_prompts()["system"].format(long_term_preferences=rendered)
