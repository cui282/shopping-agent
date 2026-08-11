from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.schemas import (
    CalculationExclusion,
    Candidate,
    ExchangeRateProvenance,
    PriceCompareOutput,
    PricePoint,
)

FX_CALCULATION_BASIS = "original_amount * rate_to_cny"
_CNY_CENT = Decimal("0.01")


class MissingExchangeRatesError(ValueError):
    def __init__(self, currencies: set[str]) -> None:
        self.currencies = tuple(sorted(currencies))
        super().__init__("missing exchange rates for candidate currencies")


async def price_compare(
    candidates: list[Candidate],
    base_currency: str = "CNY",
    top_n: int = 12,
    *,
    calculated_at: datetime | None = None,
) -> PriceCompareOutput:
    """Normalize marketplace prices to CNY without adding shipping or duties."""

    if base_currency != "CNY":
        raise ValueError("only CNY is currently supported as base_currency")
    calculation_time = calculated_at or datetime.now(timezone.utc)
    if calculation_time.tzinfo is None or calculation_time.utcoffset() is None:
        raise ValueError("calculated_at must include a timezone")
    calculation_time = calculation_time.astimezone(timezone.utc)
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
        rate = 1.0 if currency == "CNY" else None
        exclusion_reason_code = "missing_fx_evidence"
        exclusion_reason = "数据通道未提供带来源和观测时间的人民币换算报价，已排除计算和排序。"
        if currency != "CNY":
            quote = item.price_conversion
            if quote is not None and item.source == "live" and quote.expires_at is None:
                rate = None
                exclusion_reason_code = "invalid_fx_evidence"
                exclusion_reason = "线上人民币换算报价未提供有效期，已排除计算和排序。"
            elif quote is not None and quote.source_currency != currency:
                rate = None
                exclusion_reason_code = "invalid_fx_evidence"
                exclusion_reason = (
                    f"换算报价源币种为 {quote.source_currency}，与商品币种 {currency} 不一致，"
                    "已排除计算和排序。"
                )
            elif (
                quote is not None
                and quote.expires_at is not None
                and datetime.fromisoformat(quote.expires_at.replace("Z", "+00:00"))
                <= calculation_time
            ):
                rate = None
                exclusion_reason_code = "invalid_fx_evidence"
                exclusion_reason = (
                    f"人民币换算报价已过期（有效期至 {quote.expires_at}），已排除计算和排序。"
                )
            elif quote is not None:
                rate = quote.rate_to_cny
        if rate is None:
            excluded_currencies.add(currency)
            calculation_exclusions.append(
                CalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    currency=currency,
                    amount=float(amount),
                    reason_code=exclusion_reason_code,
                    reason=exclusion_reason,
                )
            )
            continue
        ranked.append(
            PricePoint(
                item_id=item.item_id,
                platform=item.platform,
                title=item.title,
                price=item.price,
                currency=currency,
                price_cny=float(
                    (Decimal(str(amount)) * Decimal(str(rate))).quantize(
                        _CNY_CENT,
                        rounding=ROUND_HALF_UP,
                    )
                ),
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
                price_conversion=item.price_conversion if currency != "CNY" else None,
                shipping_quote=item.shipping_quote,
                customs=item.customs,
            )
        )
    fx_exclusion_count = sum(
        item.reason_code in {"unsupported_currency", "missing_fx_evidence", "invalid_fx_evidence"}
        for item in calculation_exclusions
    )
    if (
        candidates
        and not ranked
        and valid_amount_count
        and fx_exclusion_count == valid_amount_count
    ):
        raise MissingExchangeRatesError(excluded_currencies)
    ranked.sort(key=lambda item: (item.price_cny, item.platform, item.item_id))
    all_ranked = ranked
    quotes = [
        item.price_conversion
        for item in all_ranked
        if item.currency.upper() != "CNY" and item.price_conversion is not None
    ]
    rate_source = "offer-level-quotes" if quotes else "native-CNY"
    rates_as_of = max((quote.observed_at for quote in quotes), default="not-applicable")
    rate_providers = sorted({quote.provider for quote in quotes})
    ranked = all_ranked[: max(1, min(top_n, 30))]
    cheapest: dict[str, PricePoint] = {}
    for item in all_ranked:
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
            providers=rate_providers,
            quote_count=len(quotes),
        ),
        excluded_currencies=sorted(excluded_currencies),
        calculation_exclusions=calculation_exclusions,
    )
