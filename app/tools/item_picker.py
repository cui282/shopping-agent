from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.schemas import (
    ItemPickerOutput,
    RememberedPreference,
    ShippingCalcOutput,
    ShoppingPlan,
)
from app.tools.decision_engine import decision_engine


async def item_picker(
    shipping: ShippingCalcOutput,
    plan: ShoppingPlan,
    max_items: int = 3,
    remembered_preferences: RememberedPreference | Mapping[str, Any] | None = None,
) -> ItemPickerOutput:
    """Classify landed offers through the deterministic Product Evidence seam."""

    return decision_engine(
        plan,
        remembered_preferences,
        shipping.items,
        max_items=max_items,
    )
