"""Operator-owned aliases for category names used by the knowledge-card pipeline."""

from __future__ import annotations

from dataclasses import dataclass

CATEGORY_ALIASES: dict[str, str] = {
    "耳机": "headphones",
    "耳麦": "headphones",
    "降噪耳机": "headphones noise cancelling",
    "相机": "camera photo",
    "数码相机": "digital cameras",
    "背包": "bags cases",
    "键盘": "computer keyboard",
    "鼠标": "computer mouse",
    "显示器": "computer monitor",
    "路由器": "networking products",
    "旅行收纳": "旅行三件套",
    "便携收纳包": "旅行三件套",
    "出差三件套": "旅行三件套",
    "马克杯": "咖啡杯",
}


@dataclass(frozen=True, slots=True)
class CategorySearchContext:
    """Deterministic search hints owned by the category taxonomy."""

    standard_category: str
    query_expansions: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()

    @property
    def query_terms(self) -> tuple[str, ...]:
        values = (self.standard_category, *self.query_expansions, *self.subtypes)
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


CATEGORY_SEARCH_CONTEXTS: dict[str, CategorySearchContext] = {
    "耳机": CategorySearchContext(
        standard_category="耳机",
        query_expansions=("headphones", "wireless headphones", "noise cancelling headphones"),
        subtypes=("头戴式", "入耳式", "真无线"),
    ),
    "键盘": CategorySearchContext(
        standard_category="键盘",
        query_expansions=("keyboard", "computer keyboard", "wireless keyboard"),
        subtypes=("机械键盘", "薄膜键盘", "静音键盘"),
    ),
    "旅行三件套": CategorySearchContext(
        standard_category="旅行收纳套装",
        query_expansions=(
            "旅行三件套",
            "旅行收纳套装",
            "旅行收纳袋",
            "packing cubes",
            "travel organizer set",
            "luggage organizer",
        ),
        subtypes=("收纳袋", "压缩收纳袋", "鞋袋", "洗漱包"),
    ),
}


def normalize_category(raw: str) -> str:
    """Normalize known aliases without asking a model to invent a category mapping."""

    value = raw.strip().casefold()
    return CATEGORY_ALIASES.get(value, value)


def category_search_context(raw: str) -> CategorySearchContext:
    """Return operator-owned search terms for a raw or normalized category."""

    value = raw.strip().casefold()
    normalized = normalize_category(value)
    context = CATEGORY_SEARCH_CONTEXTS.get(value) or CATEGORY_SEARCH_CONTEXTS.get(normalized)
    if context is not None:
        return context
    return CategorySearchContext(standard_category=normalized)


__all__ = [
    "CATEGORY_ALIASES",
    "CATEGORY_SEARCH_CONTEXTS",
    "CategorySearchContext",
    "category_search_context",
    "normalize_category",
]
