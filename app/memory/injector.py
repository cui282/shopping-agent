from __future__ import annotations

import re
from typing import Any

from app.schemas import ShoppingPlan


def merge_preferences(plan: ShoppingPlan, remembered: dict[str, Any]) -> ShoppingPlan:
    values = plan.model_dump()
    for field in ("material_preferences", "style_preferences", "soft_preferences"):
        combined = list(dict.fromkeys([*remembered.get(field, []), *values[field]]))
        values[field] = combined
    return ShoppingPlan.model_validate(values)


def extract_preferences(query: str, plan: ShoppingPlan) -> dict[str, Any]:
    preferences: dict[str, Any] = {
        "material_preferences": plan.material_preferences,
        "style_preferences": plan.style_preferences,
        "soft_preferences": plan.soft_preferences,
    }
    avoided = [
        match.group(1).strip()
        for match in re.finditer(r"(?:不要|不含|避免)([^，。；,;]{1,12})", query)
    ]
    if avoided:
        preferences["avoid"] = avoided
    return {key: value for key, value in preferences.items() if value}


def merge_preference_records(
    remembered: dict[str, Any], extracted: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(remembered)
    for key, value in extracted.items():
        if isinstance(value, list):
            previous = merged.get(key, [])
            previous_values = previous if isinstance(previous, list) else []
            merged[key] = list(dict.fromkeys([*previous_values, *value]))
        else:
            merged[key] = value
    return merged
