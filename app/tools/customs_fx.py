"""China Customs monthly assessment-rate adapter backed by PBOC public data."""

from __future__ import annotations

import asyncio
import functools
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx

from app.schemas import CustomsExchangeRateEvidence

PBOC_RATE_URL_TEMPLATE = "https://wzdt.pbc.gov.cn/huilv/flex-xml/flex_xml_{type_id}.xml"


class CustomsFxError(RuntimeError):
    """The public PBOC series cannot provide a usable assessment rate."""


@dataclass(frozen=True, slots=True)
class _PBOCRateSpec:
    type_id: int
    quote: Literal["cny_per_unit", "cny_per_100", "foreign_per_cny"]


# The PBOC pages publish the first group as CNY per foreign-currency unit, JPY per
# 100 units, and the remaining group as foreign-currency units per CNY.
_PBOC_RATES: dict[str, _PBOCRateSpec] = {
    "USD": _PBOCRateSpec(1, "cny_per_unit"),
    "EUR": _PBOCRateSpec(2, "cny_per_unit"),
    "JPY": _PBOCRateSpec(3, "cny_per_100"),
    "HKD": _PBOCRateSpec(4, "cny_per_unit"),
    "GBP": _PBOCRateSpec(5, "cny_per_unit"),
    "MYR": _PBOCRateSpec(6, "foreign_per_cny"),
    "RUB": _PBOCRateSpec(7, "foreign_per_cny"),
    "AUD": _PBOCRateSpec(8, "cny_per_unit"),
    "CAD": _PBOCRateSpec(9, "cny_per_unit"),
    "NZD": _PBOCRateSpec(10, "cny_per_unit"),
    "SGD": _PBOCRateSpec(11, "cny_per_unit"),
    "CHF": _PBOCRateSpec(12, "cny_per_unit"),
    "ZAR": _PBOCRateSpec(13, "foreign_per_cny"),
    "KRW": _PBOCRateSpec(14, "foreign_per_cny"),
    "AED": _PBOCRateSpec(15, "foreign_per_cny"),
    "SAR": _PBOCRateSpec(16, "foreign_per_cny"),
    "HUF": _PBOCRateSpec(17, "foreign_per_cny"),
    "PLN": _PBOCRateSpec(18, "foreign_per_cny"),
    "DKK": _PBOCRateSpec(19, "foreign_per_cny"),
    "SEK": _PBOCRateSpec(20, "foreign_per_cny"),
    "NOK": _PBOCRateSpec(21, "foreign_per_cny"),
    "TRY": _PBOCRateSpec(22, "foreign_per_cny"),
    "MXN": _PBOCRateSpec(23, "foreign_per_cny"),
    "THB": _PBOCRateSpec(24, "foreign_per_cny"),
    "MOP": _PBOCRateSpec(25, "foreign_per_cny"),
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_series(payload: bytes) -> dict[str, float]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise CustomsFxError("PBOC response is not valid XML") from exc
    values: dict[str, float] = {}
    for node in root.iter():
        if _local_name(node.tag) != "Temp":
            continue
        fields = {
            _local_name(child.tag): (child.text or "").strip() for child in node if child.text
        }
        observed = fields.get("date")
        raw_value = fields.get("hlvalue")
        if not observed or not raw_value:
            continue
        try:
            date.fromisoformat(observed)
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            values[observed] = value
    if not values:
        raise CustomsFxError("PBOC response did not contain exchange-rate observations")
    return values


@functools.lru_cache(maxsize=32)
def _fetch_series(type_id: int, timeout: float) -> dict[str, float]:
    response = httpx.get(
        PBOC_RATE_URL_TEMPLATE.format(type_id=type_id),
        timeout=timeout,
        headers={"Accept": "application/xml,text/xml"},
    )
    response.raise_for_status()
    return _parse_series(response.content)


def _third_wednesday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (2 - first.weekday()) % 7  # Wednesday is weekday 2.
    return first + timedelta(days=offset + 14)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _observation_candidates(declaration: date) -> tuple[date, date]:
    year, month = _previous_month(declaration.year, declaration.month)
    third = _third_wednesday(year, month)
    return third, third + timedelta(days=7)


def fetch_customs_exchange_rate(
    source_currency: str,
    *,
    declaration_date: str | date | None = None,
    timeout: float = 20,
) -> CustomsExchangeRateEvidence:
    """Return the PBOC rate used by Customs for a declaration month.

    Customs rules use the third Wednesday of the previous month, with the fourth
    Wednesday as the holiday fallback. We intentionally fail closed when neither
    observation exists instead of substituting a live market or ECB rate.
    """

    source = source_currency.strip().upper()
    if source == "CNY":
        raise CustomsFxError("CNY does not need a customs exchange rate")
    spec = _PBOC_RATES.get(source)
    if spec is None:
        raise CustomsFxError(f"PBOC customs exchange mapping is unavailable for {source}")
    if isinstance(declaration_date, date):
        declaration = declaration_date
    elif isinstance(declaration_date, str) and declaration_date.strip():
        try:
            declaration = date.fromisoformat(declaration_date.strip())
        except ValueError as exc:
            raise CustomsFxError("declaration_date must be an ISO date") from exc
    else:
        declaration = datetime.now(timezone.utc).date()
    observations = _fetch_series(spec.type_id, float(timeout))
    observed_date = next(
        (
            candidate
            for candidate in _observation_candidates(declaration)
            if candidate.isoformat() in observations
        ),
        None,
    )
    if observed_date is None:
        expected = ", ".join(item.isoformat() for item in _observation_candidates(declaration))
        raise CustomsFxError(f"PBOC series has no third/fourth Wednesday observation ({expected})")
    raw = observations[observed_date.isoformat()]
    if spec.quote == "cny_per_unit":
        rate = raw
    elif spec.quote == "cny_per_100":
        rate = raw / 100
    else:
        rate = 1 / raw
    if not math.isfinite(rate) or rate <= 0:
        raise CustomsFxError(f"PBOC rate for {source} is invalid")
    assessment_month = declaration.strftime("%Y-%m")
    source_reference = (
        f"{PBOC_RATE_URL_TEMPLATE.format(type_id=spec.type_id)}#date={observed_date.isoformat()}"
        f";customs-rule=third-wednesday-previous-month"
    )
    return CustomsExchangeRateEvidence(
        source_currency=source,
        target_currency="CNY",
        rate_to_cny=rate,
        rate_basis="monthly_customs_assessment",
        declaration_date=declaration.isoformat(),
        assessment_month=assessment_month,
        provider="pboc-benchmark-rate",
        source_reference=source_reference,
    )


async def fetch_customs_exchange_rate_async(
    source_currency: str,
    *,
    declaration_date: str | date | None = None,
    timeout: float = 20,
) -> CustomsExchangeRateEvidence:
    return await asyncio.to_thread(
        fetch_customs_exchange_rate,
        source_currency,
        declaration_date=declaration_date,
        timeout=timeout,
    )


def clear_customs_exchange_cache() -> None:
    """Clear the in-process PBOC series cache after a scheduled refresh."""

    _fetch_series.cache_clear()


__all__ = [
    "PBOC_RATE_URL_TEMPLATE",
    "CustomsFxError",
    "clear_customs_exchange_cache",
    "fetch_customs_exchange_rate",
    "fetch_customs_exchange_rate_async",
]
