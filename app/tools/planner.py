from __future__ import annotations

import re

from app.schemas import ShoppingPlan
from app.tools.query_parser import extract_budget_cny, extract_product_subject


async def planner(query: str) -> ShoppingPlan:
    """Turn free-form Chinese shopping intent into explicit constraints."""

    budget = extract_budget_cny(query)
    category = extract_product_subject(query)

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
