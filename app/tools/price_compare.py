from __future__ import annotations

import json
import math
import os

from app.schemas import Candidate, PriceCompareOutput, PricePoint

REFERENCE_FX_TO_CNY = {
    "CNY": 1.0,
    "USD": 7.18,
    "SGD": 5.32,
    "GBP": 9.05,
    "EUR": 7.78,
    "JPY": 0.046,
}


class MissingExchangeRatesError(ValueError):
    def __init__(self, currencies: set[str]) -> None:
        self.currencies = tuple(sorted(currencies))
        super().__init__("missing exchange rates for candidate currencies")


def _rates() -> tuple[dict[str, float], str, str]:
    configured = os.getenv("FX_RATES_JSON", "").strip()
    if not configured:
        return REFERENCE_FX_TO_CNY, "reference-table", "unspecified"
    try:
        payload = json.loads(configured)
        rates = {str(currency).upper(): float(rate) for currency, rate in payload.items()}
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("FX_RATES_JSON must be a JSON object of positive numeric rates") from exc
    if not rates or any(not math.isfinite(rate) or rate <= 0 for rate in rates.values()):
        raise ValueError("FX_RATES_JSON must contain positive rates")
    rates.setdefault("CNY", 1.0)
    return (
        rates,
        os.getenv("FX_RATE_SOURCE", "configured"),
        os.getenv("FX_RATES_AS_OF", "unspecified"),
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
    for item in candidates[:100]:
        currency = item.currency.upper()
        rate = rates.get(currency)
        if rate is None:
            excluded_currencies.add(currency)
            continue
        ranked.append(
            PricePoint(
                item_id=item.item_id,
                platform=item.platform,
                title=item.title,
                price=item.price,
                currency=item.currency,
                price_cny=round(item.price * rate, 2),
                rating=item.rating,
                sales=item.sales,
                image_url=item.image_url,
                product_url=item.product_url,
                attributes=item.attributes,
                source=item.source,
            )
        )
    if candidates and not ranked:
        raise MissingExchangeRatesError(excluded_currencies)
    ranked.sort(key=lambda item: item.price_cny)
    ranked = ranked[: max(1, min(top_n, 30))]
    cheapest: dict[str, PricePoint] = {}
    for item in ranked:
        cheapest.setdefault(item.platform, item)
    return PriceCompareOutput(
        ranked=ranked,
        cheapest_per_platform=cheapest,
        rate_source=rate_source,
        rates_as_of=rates_as_of,
        excluded_currencies=sorted(excluded_currencies),
    )
