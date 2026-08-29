"""Read-only adapter for China's official customs tariff query service."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

OFFICIAL_CUSTOMS_BASE_URL = os.getenv(
    "CHINA_CUSTOMS_BASE_URL", "https://online.customs.gov.cn/ociswebserver"
).rstrip("/")


class ChinaCustomsError(RuntimeError):
    """The official customs query did not return a usable response."""


@dataclass(frozen=True, slots=True)
class ChinaCustomsRates:
    hs_code: str
    goods_name: str
    mfn_rate: float | None
    ordinary_rate: float | None
    temporary_rate: float | None
    consumption_tax_rate: float | None
    import_vat_rate: float | None
    agreement_rate: float | None
    agreement_name: str | None
    effective_date: str
    source_reference: str


def _percent(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.rstrip("%")) / 100
    except ValueError:
        return None


def _parse_inner_response(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ChinaCustomsError("customs response is not a JSON object")
    inner = payload.get("res")
    if isinstance(inner, dict):
        parsed = inner
    elif isinstance(inner, str):
        # The official page currently returns a JSON-like string with unquoted keys.
        normalized = re.sub(r"([,{])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', inner)
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise ChinaCustomsError("customs response contains an invalid result payload") from exc
    else:
        raise ChinaCustomsError("customs response did not contain a result payload")
    if not isinstance(parsed, dict):
        raise ChinaCustomsError("customs result payload is not an object")
    rows = parsed.get("data", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _request(
    client: httpx.Client, endpoint: str, values: dict[str, Any], *, timeout: float
) -> list[dict[str, Any]]:
    response = client.post(
        f"{OFFICIAL_CUSTOMS_BASE_URL}/ocis/taxRateQuery/query/{endpoint}",
        data={"param": json.dumps(values, ensure_ascii=False)},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://online.customs.gov.cn/ociswebserver/pages/jckspsl/detail.html",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return _parse_inner_response(response.json())


_AGREEMENT_COUNTRIES: dict[str, tuple[str, ...]] = {
    "22": ("BN", "MM", "KH", "ID", "JP", "LA", "MY", "PH", "SG", "KR", "TH", "VN", "AU", "NZ"),
    "18": ("AU",),
    "19": ("KR",),
    "17": ("CH",),
    "16": ("IS",),
    "15": ("CR",),
    "12": ("PE",),
    "21": ("MU",),
    "24": ("NI",),
    "25": ("EC",),
    "26": ("RS",),
    "23": ("KH",),
    "2": ("BN", "KH", "ID", "LA", "MY", "MM", "PH", "SG", "TH", "VN"),
}
_COUNTRY_NAMES = {
    "AU": ("澳大利亚", "澳洲"),
    "BN": ("文莱",),
    "CH": ("瑞士",),
    "CR": ("哥斯达黎加",),
    "EC": ("厄瓜多尔",),
    "ID": ("印度尼西亚", "印尼"),
    "IS": ("冰岛",),
    "JP": ("日本",),
    "KH": ("柬埔寨",),
    "KR": ("韩国",),
    "LA": ("老挝",),
    "MM": ("缅甸",),
    "MU": ("毛里求斯",),
    "MY": ("马来西亚",),
    "NI": ("尼加拉瓜",),
    "NZ": ("新西兰",),
    "PE": ("秘鲁",),
    "PH": ("菲律宾",),
    "RS": ("塞尔维亚",),
    "SG": ("新加坡",),
    "TH": ("泰国",),
    "VN": ("越南",),
}


def _agreement_for_origin(
    client: httpx.Client, *, hs_code: str, day: str, origin_country: str, timeout: float
) -> tuple[float | None, str | None]:
    country = origin_country.strip().upper()
    aliases = {alias for alias in _COUNTRY_NAMES.get(country, ())}
    if not aliases:
        return None, None
    for agreement_code, countries in _AGREEMENT_COUNTRIES.items():
        if country not in countries:
            continue
        rows = _request(
            client,
            "queryCountryAgreementRate",
            {"codeTs": hs_code, "sysDate": day, "agreementCode": agreement_code},
            timeout=timeout,
        )
        for row in rows:
            name = str(row.get("conuntryName") or "").strip()
            if name in aliases:
                rate = _percent(row.get("impAgreementRate"))
                if rate is not None:
                    return rate, f"agreement:{agreement_code}:{name}"
    return None, None


def lookup_rates(
    hs_code: str,
    *,
    goods_name: str = "",
    origin_country: str = "",
    effective_date: str | None = None,
    timeout: float = 20,
) -> ChinaCustomsRates:
    """Query official tariff/VAT data for one explicitly supplied HS code."""

    code = hs_code.strip()
    if not re.fullmatch(r"\d{6,10}", code):
        raise ChinaCustomsError("hs_code must contain 6 to 10 digits")
    day = effective_date or datetime.now(timezone.utc).date().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise ChinaCustomsError("effective_date must be an ISO date") from exc
    with httpx.Client() as client:
        tariff_rows = _request(
            client,
            "queryImpTariffRate",
            {
                "gName": goods_name,
                "codeTs": code,
                "sysDate": day,
                "nextPage": "1",
                "pageSize": "10",
            },
            timeout=timeout,
        )
        tariff = next((row for row in tariff_rows if str(row.get("codeTs", "")) == code), None)
        if tariff is None and goods_name.strip():
            # The public endpoint treats gName as an exact Chinese-name filter. Retry by
            # classification when a provider title is localized or uses a brand name.
            tariff_rows = _request(
                client,
                "queryImpTariffRate",
                {
                    "gName": "",
                    "codeTs": code,
                    "sysDate": day,
                    "nextPage": "1",
                    "pageSize": "10",
                },
                timeout=timeout,
            )
        tax_rows = _request(
            client,
            "queryTaxRate",
            {"codeTs": code, "sysDate": day},
            timeout=timeout,
        )
        agreement_rate, agreement_name = _agreement_for_origin(
            client,
            hs_code=code,
            day=day,
            origin_country=origin_country,
            timeout=timeout,
        )
    tariff = next((row for row in tariff_rows if str(row.get("codeTs", "")) == code), None)
    if tariff is None and tariff_rows:
        tariff = tariff_rows[0]
    if tariff is None:
        raise ChinaCustomsError(f"official customs service returned no tariff row for {code}")
    tax = tax_rows[0] if tax_rows else {}
    return ChinaCustomsRates(
        hs_code=code,
        goods_name=str(tariff.get("gName") or goods_name).strip(),
        mfn_rate=_percent(tariff.get("impDiscountRate")),
        ordinary_rate=_percent(tariff.get("impOrdinaryRate")),
        temporary_rate=_percent(tariff.get("impTempRate")),
        consumption_tax_rate=_percent(tax.get("impConsumeRate")),
        import_vat_rate=_percent(tax.get("impVatRate")),
        agreement_rate=agreement_rate,
        agreement_name=agreement_name,
        effective_date=day,
        source_reference=f"{OFFICIAL_CUSTOMS_BASE_URL}/pages/jckspsl/detail.html?codeTs={code}",
    )


async def lookup_rates_async(
    hs_code: str,
    *,
    goods_name: str = "",
    origin_country: str = "",
    effective_date: str | None = None,
    timeout: float = 20,
) -> ChinaCustomsRates:
    return await asyncio.to_thread(
        lookup_rates,
        hs_code,
        goods_name=goods_name,
        origin_country=origin_country,
        effective_date=effective_date,
        timeout=timeout,
    )


__all__ = [
    "OFFICIAL_CUSTOMS_BASE_URL",
    "ChinaCustomsError",
    "ChinaCustomsRates",
    "lookup_rates",
    "lookup_rates_async",
]
