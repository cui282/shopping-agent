from __future__ import annotations

from app.schemas import LandedCost, PricePoint, ShippingCalcOutput

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

    landed: list[LandedCost] = []
    for item in items[:12]:
        weight_raw = item.attributes.get("weight_kg")
        weight_is_known = isinstance(weight_raw, (int, float))
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
            )
        )
    landed.sort(key=lambda item: item.landed_cny)
    return ShippingCalcOutput(
        destination=destination,
        items=landed,
        calculation_basis="按平台和重量区间的可配置规则估算",
    )
