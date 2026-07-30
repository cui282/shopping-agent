from __future__ import annotations

import re

from app.schemas import ShoppingPlan

_CATEGORY_TERMS = {
    "耳机": ("耳机", "降噪", "头戴", "入耳"),
    "咖啡机": ("咖啡机", "意式", "手冲"),
    "背包": ("背包", "双肩包", "通勤包"),
    "键盘": ("键盘", "机械键盘"),
    "运动鞋": ("运动鞋", "跑鞋", "球鞋"),
}


async def planner(query: str) -> ShoppingPlan:
    """Turn free-form Chinese shopping intent into explicit constraints."""

    budget_match = re.search(r"(?:预算|不超过|以内)[^\d]{0,4}(\d+(?:\.\d+)?)", query)
    if budget_match is None:
        budget_match = re.search(r"(\d+(?:\.\d+)?)\s*元", query)
    budget = float(budget_match.group(1)) if budget_match else None

    category = "商品"
    for name, terms in _CATEGORY_TERMS.items():
        if any(term in query for term in terms):
            category = name
            break

    hard_constraints: list[str] = []
    material_preferences: list[str] = []
    for match in re.finditer(r"(?:不要|不含|避免)([^，。；,;]{1,12})", query):
        value = match.group(1).strip()
        hard_constraints.append(f"避免{value}")
        material_preferences.append(f"不含{value}")
    if budget is not None:
        hard_constraints.append(f"到手价不超过{budget:.0f}元")

    soft_map = ("轻便", "降噪", "小众", "耐用", "便携", "无线", "舒适", "通勤")
    soft_preferences = [term for term in soft_map if term in query]
    style_preferences = [term for term in ("简约", "复古", "运动", "商务") if term in query]

    destination = "中国大陆"
    destination_match = re.search(r"(?:寄到|送到|配送到)([^，。；,;]{2,12})", query)
    if destination_match:
        destination = destination_match.group(1).strip()

    return ShoppingPlan(
        budget_cny=budget,
        category=category,
        material_preferences=material_preferences,
        style_preferences=style_preferences,
        hard_constraints=hard_constraints,
        soft_preferences=soft_preferences,
        destination=destination,
    )
