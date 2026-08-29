"""Customs evidence adapters with an official China tariff-rate baseline."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.schemas import Candidate, CustomsTaxEvidence, CustomsValuationEvidence
from app.tools.china_customs import ChinaCustomsError, ChinaCustomsRates, lookup_rates_async
from app.tools.customs_fx import CustomsFxError, fetch_customs_exchange_rate_async


class CustomsEvidenceError(RuntimeError):
    """Customs evidence is missing or cannot be validated."""


@dataclass(frozen=True, slots=True)
class CustomsLookupInput:
    hs_code: str
    country_of_origin: str
    import_regime: str = "general_trade"
    rate_type: str = "mfn"
    ship_from_country: str | None = None


def _money(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _rate(rates: ChinaCustomsRates, rate_type: str) -> float:
    if rate_type == "agreement":
        value = rates.agreement_rate
    elif rate_type == "temporary":
        value = rates.temporary_rate
    else:
        value = rates.mfn_rate if rate_type == "mfn" else rates.ordinary_rate
    if value is None:
        raise CustomsEvidenceError(f"official customs service has no {rate_type} tariff rate")
    return value


def _input_from_candidate(candidate: Candidate) -> CustomsLookupInput:
    attributes: dict[str, Any] = candidate.attributes
    hs_code = str(attributes.get("hs_code") or "").strip()
    origin = str(attributes.get("country_of_origin") or "").strip()
    if not hs_code or not origin:
        raise CustomsEvidenceError("candidate must contain explicit hs_code and country_of_origin")
    regime = os.getenv("CUSTOMS_IMPORT_REGIME", "").strip()
    rate_type = os.getenv("CUSTOMS_RATE_TYPE", "mfn").strip() or "mfn"
    if not regime:
        raise CustomsEvidenceError("CUSTOMS_IMPORT_REGIME is not configured")
    if rate_type not in {"auto", "agreement", "mfn", "ordinary", "temporary"}:
        raise CustomsEvidenceError(
            "official tariff baseline supports CUSTOMS_RATE_TYPE=auto, agreement, mfn, ordinary, or temporary"
        )
    return CustomsLookupInput(
        hs_code=hs_code,
        country_of_origin=origin,
        import_regime=regime,
        rate_type=rate_type,
        ship_from_country=str(attributes.get("ship_from_country") or "").strip() or None,
    )


async def enrich_general_trade(
    candidate: Candidate,
    *,
    shipping_cny: float,
    effective_date: str | None = None,
) -> Candidate:
    """Add official tariff/VAT evidence when the candidate supplies explicit classification data."""

    lookup = _input_from_candidate(candidate)
    if lookup.import_regime != "general_trade":
        raise CustomsEvidenceError(
            "official tariff lookup only builds general_trade evidence; other regimes need provider eligibility"
        )
    try:
        rates = await lookup_rates_async(
            lookup.hs_code,
            goods_name=candidate.title,
            origin_country=lookup.country_of_origin,
            effective_date=effective_date,
        )
    except ChinaCustomsError as exc:
        raise CustomsEvidenceError(str(exc)) from exc
    if rates.import_vat_rate is None:
        raise CustomsEvidenceError("official customs service returned no import VAT rate")
    effective_rate_type = lookup.rate_type
    if effective_rate_type == "auto":
        if rates.agreement_rate is not None:
            effective_rate_type = "agreement"
        elif rates.temporary_rate is not None:
            effective_rate_type = "temporary"
        else:
            effective_rate_type = "mfn"
    tariff_rate = _rate(rates, effective_rate_type)
    consumption = rates.consumption_tax_rate or 0.0
    goods_value = _money(candidate.price)
    freight = _money(shipping_cny)
    customs_conversion = None
    goods_currency = candidate.currency.upper()
    if goods_currency == "CNY":
        goods_value_cny = goods_value
    else:
        try:
            customs_conversion = await fetch_customs_exchange_rate_async(
                goods_currency,
                declaration_date=rates.effective_date,
            )
        except CustomsFxError as exc:
            raise CustomsEvidenceError(str(exc)) from exc
        goods_value_cny = _money(goods_value * customs_conversion.rate_to_cny)
    customs_value = _money(goods_value_cny + freight)
    valuation = CustomsValuationEvidence(
        goods_value_original=goods_value,
        goods_currency=goods_currency,
        goods_value_cny=goods_value_cny,
        international_shipping_cny=freight,
        insurance_cny=0,
        customs_value_cny=customs_value,
        customs_conversion=customs_conversion,
        provider="china-customs-official",
        source_reference=rates.source_reference,
    )
    evidence = CustomsTaxEvidence(
        hs_code=lookup.hs_code,
        country_of_origin=lookup.country_of_origin,
        destination_country="CN",
        ship_from_country=lookup.ship_from_country,
        import_regime="general_trade",
        rate_type=effective_rate_type,  # type: ignore[arg-type]
        tariff_rate=tariff_rate,
        import_vat_rate=rates.import_vat_rate,
        consumption_tax_rate=consumption,
        valuation=valuation,
        provider="china-customs-official",
        source_reference=rates.source_reference,
        effective_date=rates.effective_date,
    )
    attributes = dict(candidate.attributes)
    attributes["customs_rate_source"] = rates.source_reference
    attributes["customs_rate_effective_date"] = rates.effective_date
    attributes["customs_rate_type"] = lookup.rate_type
    if rates.agreement_name:
        attributes["customs_agreement"] = rates.agreement_name
    return candidate.model_copy(update={"attributes": attributes, "customs": evidence})


async def enrich_candidates(candidates: list[Candidate], *, max_items: int = 12) -> list[Candidate]:
    """Optionally enrich explicitly classified candidates before price comparison.

    The default is off because an eBay listing rarely carries a legally sufficient HS/origin pair.
    Enabling it never fabricates evidence: candidates without a complete input remain unchanged.
    """

    if os.getenv("CUSTOMS_AUTO_LOOKUP", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return candidates
    selected = list(candidates)

    async def enrich(candidate: Candidate) -> Candidate:
        if candidate.customs is not None:
            return candidate
        shipping = shipping_amount_cny(candidate)
        if shipping is None:
            return candidate
        try:
            return await enrich_general_trade(candidate, shipping_cny=shipping)
        except CustomsEvidenceError:
            return candidate

    limit = max(0, min(max_items, len(selected)))
    prefix = await asyncio.gather(*(enrich(candidate) for candidate in selected[:limit]))
    return [*prefix, *selected[limit:]]


def shipping_amount_cny(candidate: Candidate) -> float | None:
    """Return only native-CNY freight for automatic customs enrichment.

    A comparison-rate conversion is not a customs assessment rate. Non-CNY freight
    remains eligible for ordinary shipping comparison, but needs a provider-supplied
    customs valuation before it can be used for customs tax enrichment.
    """

    quote = candidate.shipping_quote
    if quote is None:
        return None
    if quote.currency == "CNY":
        return quote.total_amount
    return None


__all__ = [
    "CustomsEvidenceError",
    "CustomsLookupInput",
    "enrich_candidates",
    "enrich_general_trade",
    "shipping_amount_cny",
]
