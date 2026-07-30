from __future__ import annotations

from app.schemas import ItemPickerOutput, Recommendation, ShippingCalcOutput, ShoppingPlan


def _violates(item: object, plan: ShoppingPlan) -> bool:
    landed = item.landed_cny
    attributes = item.attributes
    if plan.budget_cny is not None and landed > plan.budget_cny:
        return True
    searchable = " ".join(str(value) for value in attributes.values()).lower()
    for preference in plan.material_preferences:
        if preference.startswith("不含") and preference[2:].lower() in searchable:
            return True
    return False


async def item_picker(
    shipping: ShippingCalcOutput, plan: ShoppingPlan, max_items: int = 3
) -> ItemPickerOutput:
    """Apply hard constraints first, then rank the surviving landed-cost options."""

    eligible = [item for item in shipping.items if not _violates(item, plan)]
    eligible.sort(
        key=lambda item: (
            item.landed_cny,
            -(item.rating if item.rating is not None else 0),
            -(item.sales if item.sales is not None else 0),
        )
    )
    picks: list[Recommendation] = []
    for rank, item in enumerate(eligible[: max(1, min(max_items, 3))], start=1):
        reason_parts = [f"到手约¥{item.landed_cny:.0f}", f"预计{item.eta_days}天"]
        if item.rating is not None:
            reason_parts.append(f"评分{item.rating:.1f}")
        matching = [
            term
            for term in plan.soft_preferences
            if term in item.title or term in str(item.attributes)
        ]
        if matching:
            reason_parts.append(f"符合{matching[0]}")
        picks.append(Recommendation(**item.model_dump(), reason="，".join(reason_parts), rank=rank))
    return ItemPickerOutput(
        recommendations=picks,
        rejected_count=len(shipping.items) - len(eligible),
    )
