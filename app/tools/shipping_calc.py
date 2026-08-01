from __future__ import annotations

import math

from app.schemas import EstimateDisclosure, LandedCost, PricePoint, ShippingCalcOutput
from app.tools.destination import (
    SUPPORTED_DESTINATION,
    UnsupportedDestinationError,
    normalize_destination,
)

_DUTY = {"amazon": 0.13, "shopee": 0.06, "aliexpress": 0.13, "ebay": 0.20}
_SHIPPING = {
    "amazon": [(0, 85, 12), (0.5, 130, 10), (2, 240, 8)],
    "shopee": [(0, 35, 9), (0.5, 60, 9), (2, 120, 7)],
    "aliexpress": [(0, 20, 25), (0.5, 40, 22), (2, 90, 18)],
    "ebay": [(0, 90, 14), (0.5, 150, 12), (2, 300, 10)],
}


def _shipping_for(platform: str, weight: float) -> tuple[float, int]:
    brackets = _SHIPPING[platform]
    selected = brackets[0][1:]
    for threshold, amount, eta in brackets:
        if weight >= threshold:
            selected = (amount, eta)
    return float(selected[0]), int(selected[1])


async def shipping_calc(
    items: list[PricePoint], destination: str = "中国大陆"
) -> ShippingCalcOutput:
    """Estimate shipping, duties, landed price, and delivery time."""

    destination = normalize_destination(destination)
    if destination != SUPPORTED_DESTINATION:
        raise UnsupportedDestinationError(destination)

    landed: list[LandedCost] = []
    for item in items[:12]:
        weight_raw = item.attributes.get("weight_kg")
        weight_is_known = (
            isinstance(weight_raw, (int, float))
            and not isinstance(weight_raw, bool)
            and math.isfinite(float(weight_raw))
            and float(weight_raw) >= 0
        )
        weight = float(weight_raw) if weight_is_known else 0.5
        shipping, eta = _shipping_for(item.platform, weight)
        duty_rate = _DUTY[item.platform]
        duty = round(item.price_cny * duty_rate, 2)
        tier = "免征" if duty == 0 else ("高税" if duty_rate >= 0.2 else "标准")
        item_data = item.model_dump()
        item_data["note"] = item.note or (None if weight_is_known else "重量缺失，运费按0.5kg估算")
        landed.append(
            LandedCost(
                **item_data,
                shipping_cny=shipping,
                duty_cny=duty,
                landed_cny=round(item.price_cny + shipping + duty, 2),
                eta_days=eta,
                duty_tier=tier,
                shipping_estimate=EstimateDisclosure(
                    estimated=True,
                    source="shipping_rules",
                    calculation_basis=(
                        "平台和重量区间；重量缺失时按0.5kg估算"
                        if not weight_is_known
                        else "平台和重量区间"
                    ),
                ),
                duty_estimate=EstimateDisclosure(
                    estimated=True,
                    source="duty_rules",
                    calculation_basis="商品价 CNY × 平台关税率",
                ),
                delivery_estimate=EstimateDisclosure(
                    estimated=True,
                    source="shipping_rules",
                    calculation_basis="平台和重量区间",
                ),
            )
        )
    landed.sort(key=lambda item: (item.landed_cny, item.platform, item.item_id))
    return ShippingCalcOutput(
        destination=destination,
        items=landed,
        calculation_basis="运费与配送时效按平台和重量区间估算；关税按平台关税率估算",
    )
