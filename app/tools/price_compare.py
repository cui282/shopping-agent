from __future__ import annotations

import json
import math
import os

from app.schemas import (
    CalculationExclusion,
    Candidate,
    ExchangeRateProvenance,
    PriceCompareOutput,
    PricePoint,
)

REFERENCE_FX_TO_CNY = {
    "CNY": 1.0,
    "USD": 7.18,
    "SGD": 5.32,
    "GBP": 9.05,
    "EUR": 7.78,
    "JPY": 0.046,
}
REFERENCE_FX_EFFECTIVE_DATE = "2026-01-01"
FX_CALCULATION_BASIS = "original_amount * rate_to_cny"


class MissingExchangeRatesError(ValueError):
    def __init__(self, currencies: set[str]) -> None:
        self.currencies = tuple(sorted(currencies))
        super().__init__("missing exchange rates for candidate currencies")


def _rates() -> tuple[dict[str, float], str, str]:
    configured = os.getenv("FX_RATES_JSON", "").strip()
    if not configured:
        return REFERENCE_FX_TO_CNY, "reference-table", REFERENCE_FX_EFFECTIVE_DATE
    try:
        payload = json.loads(configured)
        rates = {str(currency).upper(): float(rate) for currency, rate in payload.items()}
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("FX_RATES_JSON must be a JSON object of positive numeric rates") from exc
    if not rates or any(not math.isfinite(rate) or rate <= 0 for rate in rates.values()):
        raise ValueError("FX_RATES_JSON must contain positive rates")
    rates.setdefault("CNY", 1.0)
    rate_source = os.getenv("FX_RATE_SOURCE", "configured").strip() or "configured"
    rates_as_of = os.getenv("FX_RATES_AS_OF", "").strip()
    if not rates_as_of:
        raise ValueError("FX_RATES_AS_OF must be configured for custom exchange rates")
    return (
        rates,
        rate_source,
        rates_as_of,
    )


async def price_compare(
    candidates: list[Candidate], base_currency: str = "CNY", top_n: int = 12
) -> PriceCompareOutput:
    """Normalize marketplace prices to CNY without adding shipping or duties."""

    if base_currency != "CNY":
        raise ValueError("only CNY is currently supported as base_currency")
    rates, rate_source, rates_as_of = _rates()
    ranked: list[PricePoint] = []
    excluded_currencies: set[str] = set()
    calculation_exclusions: list[CalculationExclusion] = []
    valid_amount_count = 0
    for item in candidates[:100]:
        currency = str(item.currency).strip().upper() or "UNKNOWN"
        amount = item.price if isinstance(item.price, (int, float)) else None
        if (
            isinstance(amount, bool)
            or amount is None
            or not math.isfinite(float(amount))
            or float(amount) < 0
        ):
            calculation_exclusions.append(
                CalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    currency=currency,
                    amount=None
                    if (
                        amount is None
                        or isinstance(amount, bool)
                        or not math.isfinite(float(amount))
                    )
                    else float(amount),
                    reason_code="invalid_amount",
                    reason="商品原始金额不是有限的非负数，已排除计算和排序。",
                )
            )
            continue
        valid_amount_count += 1
        rate = rates.get(currency)
        if rate is None:
            excluded_currencies.add(currency)
            calculation_exclusions.append(
                CalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    currency=currency,
                    amount=float(amount),
                    reason_code="unsupported_currency",
                    reason=f"没有可用的 {currency} 到 CNY 汇率，已排除计算和排序。",
                )
            )
            continue
        ranked.append(
            PricePoint(
                item_id=item.item_id,
                platform=item.platform,
                title=item.title,
                price=item.price,
                currency=item.currency,
                price_cny=round(float(amount) * rate, 2),
                rating=item.rating,
                sales=item.sales,
                image_url=item.image_url,
                product_url=item.product_url,
                attributes=item.attributes,
                source=item.source,
                marketplace=item.marketplace,
                offer_id=item.offer_id,
                identity=item.identity,
                variant_attributes=item.variant_attributes,
                availability=item.availability,
                retrieved_at=item.retrieved_at,
                provenance=item.provenance,
                link_kind=item.link_kind,
                identity_evidence=item.identity_evidence,
            )
        )
    unsupported_count = sum(
        item.reason_code == "unsupported_currency" for item in calculation_exclusions
    )
    if candidates and not ranked and valid_amount_count and unsupported_count == valid_amount_count:
        raise MissingExchangeRatesError(excluded_currencies)
    ranked.sort(key=lambda item: (item.price_cny, item.platform, item.item_id))
    ranked = ranked[: max(1, min(top_n, 30))]
    cheapest: dict[str, PricePoint] = {}
    for item in ranked:
        cheapest.setdefault(item.platform, item)
    return PriceCompareOutput(
        ranked=ranked,
        cheapest_per_platform=cheapest,
        rate_source=rate_source,
        rates_as_of=rates_as_of,
        exchange_rate=ExchangeRateProvenance(
            source=rate_source,
            effective_date=rates_as_of,
            calculation_basis=FX_CALCULATION_BASIS,
        ),
        excluded_currencies=sorted(excluded_currencies),
        calculation_exclusions=calculation_exclusions,
    )
