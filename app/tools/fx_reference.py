"""ECB reference-rate adapter used for transparent comparison conversions."""

from __future__ import annotations

import asyncio
import math
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import httpx

from app.schemas import Candidate, CurrencyConversionEvidence

ECB_DAILY_REFERENCE_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_ECB_NS = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"


def _parse_rates(xml_payload: bytes) -> tuple[str, dict[str, float]]:
    root = ET.fromstring(xml_payload)
    cube = next(
        (
            element
            for element in root.iter()
            if element.tag.endswith("Cube") and element.get("time")
        ),
        None,
    )
    if cube is None or not cube.get("time"):
        raise ValueError("ECB response did not contain a dated rate cube")
    rates: dict[str, float] = {"EUR": 1.0}
    for element in cube.iter(f"{{{_ECB_NS}}}Cube"):
        currency = element.get("currency")
        value = element.get("rate")
        if not currency or not value:
            continue
        try:
            rate = float(value)
        except ValueError:
            continue
        if math.isfinite(rate) and rate > 0:
            rates[currency.upper()] = rate
    return cube.get("time"), rates


def conversion_from_rates(
    source_currency: str,
    observation_date: str,
    rates: dict[str, float],
    *,
    now: datetime | None = None,
) -> CurrencyConversionEvidence | None:
    source = source_currency.strip().upper()
    if source == "CNY" or source not in rates or "CNY" not in rates:
        return None
    rate = rates["CNY"] / rates[source]
    observed = datetime.fromisoformat(f"{observation_date}T00:00:00+00:00")
    expiry = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) + timedelta(hours=24)
    return CurrencyConversionEvidence(
        source_currency=source,
        target_currency="CNY",
        rate_to_cny=rate,
        rate_type="mid_market_reference",
        markup_status="excluded",
        provider="ecb-reference-rates",
        source_reference=f"{ECB_DAILY_REFERENCE_URL}#{observation_date}",
        observed_at=observed.isoformat().replace("+00:00", "Z"),
        expires_at=expiry.isoformat().replace("+00:00", "Z"),
    )


def _fetch_xml(timeout: float) -> bytes:
    response = httpx.get(ECB_DAILY_REFERENCE_URL, timeout=timeout)
    response.raise_for_status()
    return response.content


def fetch_conversion(
    source_currency: str, *, timeout: float = 20
) -> CurrencyConversionEvidence | None:
    if source_currency.strip().upper() == "CNY":
        return None
    date_value, rates = _parse_rates(_fetch_xml(timeout))
    return conversion_from_rates(source_currency, date_value, rates)


async def fetch_conversions(
    currencies: set[str], *, timeout: float = 20
) -> dict[str, CurrencyConversionEvidence]:
    requested = {
        currency.strip().upper() for currency in currencies if currency.strip().upper() != "CNY"
    }
    if not requested:
        return {}
    response_bytes = await asyncio.to_thread(_fetch_xml, timeout)
    observation_date, rates = _parse_rates(response_bytes)
    values: dict[str, CurrencyConversionEvidence] = {}
    now = datetime.now(timezone.utc)
    for currency in requested:
        conversion = conversion_from_rates(currency, observation_date, rates, now=now)
        if conversion is not None:
            values[currency] = conversion
    return values


async def enrich_price_conversions(
    candidates: Sequence[Candidate], *, timeout: float = 20
) -> list[Candidate]:
    """Attach a current reference quote to every foreign-currency offer that lacks one.

    Product snapshots can legitimately predate the current research run.  Their timestamp is
    retained as provenance, while the comparison path uses the latest available reference rate
    so an old catalog row is still comparable with a live marketplace offer.
    """

    values = list(candidates)
    now = datetime.now(timezone.utc)
    currencies: set[str] = set()
    for candidate in values:
        currency = candidate.currency.strip().upper()
        if currency == "CNY":
            continue
        current = candidate.price_conversion
        needs_refresh = current is None or current.source_currency != currency
        if current is not None and candidate.source == "live":
            if current.expires_at is None:
                needs_refresh = True
            else:
                current_expiry = datetime.fromisoformat(current.expires_at.replace("Z", "+00:00"))
                needs_refresh = needs_refresh or current_expiry <= now
        if needs_refresh:
            currencies.add(currency)
    if not currencies:
        return values
    try:
        fetched = await fetch_conversions(currencies, timeout=timeout)
    except Exception:  # noqa: BLE001 - price comparison remains usable with existing quotes
        return values

    enriched: list[Candidate] = []
    for candidate in values:
        currency = candidate.currency.strip().upper()
        replacement = fetched.get(currency)
        current = candidate.price_conversion
        current_expired = False
        if current is not None and current.expires_at is not None:
            current_expired = (
                datetime.fromisoformat(current.expires_at.replace("Z", "+00:00")) <= now
            )
        replace_current = current is None or current.source_currency != currency
        if current is not None and candidate.source == "live":
            replace_current = replace_current or current.expires_at is None or current_expired
        if replacement is not None and replace_current:
            enriched.append(candidate.model_copy(update={"price_conversion": replacement}))
        else:
            enriched.append(candidate)
    return enriched


__all__ = [
    "ECB_DAILY_REFERENCE_URL",
    "conversion_from_rates",
    "enrich_price_conversions",
    "fetch_conversion",
    "fetch_conversions",
]
