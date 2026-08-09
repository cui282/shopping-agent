"""Operator-owned aliases for category names used by the knowledge-card pipeline."""

from __future__ import annotations

CATEGORY_ALIASES: dict[str, str] = {
    "旅行收纳": "旅行三件套",
    "便携收纳包": "旅行三件套",
    "出差三件套": "旅行三件套",
    "马克杯": "咖啡杯",
}


def normalize_category(raw: str) -> str:
    """Normalize known aliases without asking a model to invent a category mapping."""

    value = raw.strip().casefold()
    return CATEGORY_ALIASES.get(value, value)


__all__ = ["CATEGORY_ALIASES", "normalize_category"]
