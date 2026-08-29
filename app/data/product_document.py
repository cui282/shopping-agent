"""Normalize marketplace snapshots into the OpenSearch product document shape."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.schemas import (
    Candidate,
    CurrencyConversionEvidence,
    CustomsTaxEvidence,
    IdentityEvidence,
    OfferProvenance,
    ProductIdentity,
    ShippingQuoteEvidence,
)

PLATFORMS = {"amazon", "shopee", "aliexpress", "ebay"}
PRODUCT_SOURCES = {"live", "fixture"}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _int(value: Any) -> int | None:
    number = _float(value)
    return int(number) if number is not None and number.is_integer() else None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    values = [_text(item) for item in value]
    return list(dict.fromkeys(item for item in values if item))


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = _text(key)
        if not name:
            continue
        if isinstance(item, Mapping):
            result[name] = _mapping(item)
        elif isinstance(item, (list, tuple)):
            result[name] = [_json_value(part) for part in item]
        else:
            result[name] = _json_value(item)
    return result


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return _mapping(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return _text(value) or None


def _timestamp(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _default_expiry(source: str, snapshot_at: str | None) -> str | None:
    if source != "live":
        return None
    base = _timestamp(snapshot_at)
    parsed = datetime.fromisoformat(base.replace("Z", "+00:00")) if base else _now()
    return (parsed + timedelta(hours=6)).isoformat().replace("+00:00", "Z")


def _price_cny(
    raw: Mapping[str, Any],
    *,
    price: float,
    currency: str,
    price_conversion: Mapping[str, Any] | None,
) -> float | None:
    """Materialize the scalar CNY field used by OpenSearch budget filters."""

    explicit = _float(raw.get("price_cny"))
    if explicit is not None:
        return explicit
    if currency == "CNY":
        return float(Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if not price_conversion:
        return None
    source = _text(price_conversion.get("source_currency")).upper()
    target = _text(price_conversion.get("target_currency")).upper() or "CNY"
    rate = _float(price_conversion.get("rate_to_cny"))
    if source != currency or target != "CNY" or rate is None or rate <= 0:
        return None
    return float(
        (Decimal(str(price)) * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def normalize_product_record(
    raw: Mapping[str, Any],
    *,
    default_platform: str | None = None,
    default_source: str = "live",
    default_provider: str = "marketplace-snapshot",
    snapshot_at: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any] | None:
    """Return a strict, indexable product record or ``None`` for incomplete input."""

    platform = (_text(raw.get("platform")) or default_platform or "").lower()
    if platform not in PLATFORMS:
        return None
    source = (_text(raw.get("source")) or default_source).lower()
    if source not in PRODUCT_SOURCES:
        return None
    item_id = _text(raw.get("item_id")) or _text(raw.get("itemId"))
    title = _text(raw.get("title")) or _text(raw.get("name"))
    price = _float(raw.get("price"))
    currency = (_text(raw.get("currency")) or _text(raw.get("currency_code"))).upper()
    if not item_id or not title or price is None or not currency:
        return None

    snapshot = (
        _timestamp(raw.get("snapshot_at"))
        or _timestamp(snapshot_at)
        or _timestamp(raw.get("retrieved_at"))
    )
    if snapshot is None:
        snapshot = _now().isoformat().replace("+00:00", "Z")
    expiry = _timestamp(raw.get("expires_at")) or _timestamp(expires_at)
    if expiry is None:
        expiry = _default_expiry(source, snapshot)
    categories = _strings(raw.get("categories") or raw.get("category_names"))
    category_ids = _strings(raw.get("category_ids"))
    description = _text(raw.get("description"))
    brand = _text(raw.get("brand")) or None
    searchable_text = " | ".join(
        part
        for part in (
            title,
            brand or "",
            " / ".join(categories),
            description,
            " ".join(_strings(raw.get("keywords"))),
        )
        if part
    )[:8000]
    attributes = _mapping(raw.get("attributes"))
    for key in ("condition", "seller", "buying_options", "shipping_country"):
        value = raw.get(key)
        if value is not None and key not in attributes:
            attributes[key] = _json_value(value)
    price_conversion = _mapping(raw.get("price_conversion")) or None
    record = {
        "item_id": item_id,
        "offer_id": _text(raw.get("offer_id")) or item_id,
        "platform": platform,
        "marketplace": platform,
        "marketplace_id": _text(raw.get("marketplace_id")) or None,
        "title": title,
        "brand": brand,
        "categories": categories,
        "category_ids": category_ids,
        "category_text": " / ".join(categories),
        "description": description,
        "searchable_text": searchable_text,
        "price": price,
        "price_cny": _price_cny(
            raw,
            price=price,
            currency=currency,
            price_conversion=price_conversion,
        ),
        "currency": currency,
        "rating": _float(raw.get("rating")),
        "sales": _int(raw.get("sales")),
        "image_url": _text(raw.get("image_url")) or None,
        "product_url": _text(raw.get("product_url")) or None,
        "availability": _text(raw.get("availability")) or "unknown",
        "condition": _text(raw.get("condition")) or None,
        "is_active": bool(raw.get("is_active", True)),
        "source": source,
        "provider": _text(raw.get("provider")) or default_provider,
        "upstream_source": _text(raw.get("upstream_source")) or None,
        "snapshot_at": snapshot,
        "expires_at": expiry,
        "retrieved_at": _timestamp(raw.get("retrieved_at")) or snapshot,
        "gtin": _text(raw.get("gtin")) or None,
        "hs_code": _text(raw.get("hs_code")) or None,
        "country_of_origin": _text(raw.get("country_of_origin")) or None,
        "ship_from_country": _text(raw.get("ship_from_country")) or None,
        "mpn": _text(raw.get("mpn")) or None,
        "model": _text(raw.get("model")) or None,
        "attributes": attributes,
        "variant_attributes": _mapping(raw.get("variant_attributes")),
        "price_conversion": price_conversion,
        "shipping_quote": _mapping(raw.get("shipping_quote")) or None,
        "customs": _mapping(raw.get("customs")) or None,
    }
    return {key: value for key, value in record.items() if value is not None}


def candidate_from_product_record(
    record: Mapping[str, Any], score: float | None = None
) -> Candidate:
    """Project one OpenSearch hit into the existing Product Evidence contract."""

    platform = _text(record.get("platform")).lower()
    source = _text(record.get("source")).lower()
    provider = _text(record.get("provider")) or "opensearch-product-index"
    provenance = OfferProvenance(
        kind="sandbox_fixture" if source == "fixture" else "marketplace_gateway",
        provider=provider,
        upstream_source=_text(record.get("upstream_source")) or "opensearch-product-snapshot",
    )
    attributes = _mapping(record.get("attributes"))
    if score is not None:
        attributes["opensearch_score"] = float(score)
    for key in ("marketplace_id", "snapshot_at", "expires_at"):
        value = record.get(key)
        if value is not None:
            attributes[key] = _json_value(value)
    for key in ("hs_code", "country_of_origin", "ship_from_country"):
        value = record.get(key)
        if value is not None:
            attributes[key] = _json_value(value)
    identity = ProductIdentity(
        gtin=_text(record.get("gtin")) or None,
        mpn=_text(record.get("mpn")) or None,
        brand=_text(record.get("brand")) or None,
        model=_text(record.get("model")) or None,
    )
    price_conversion = None
    if isinstance(record.get("price_conversion"), Mapping):
        try:
            price_conversion = CurrencyConversionEvidence.model_validate(record["price_conversion"])
        except ValueError:
            price_conversion = None
    shipping_quote = None
    if isinstance(record.get("shipping_quote"), Mapping):
        try:
            shipping_quote = ShippingQuoteEvidence.model_validate(record["shipping_quote"])
        except ValueError:
            shipping_quote = None
    customs = None
    if isinstance(record.get("customs"), Mapping):
        try:
            customs = CustomsTaxEvidence.model_validate(record["customs"])
        except ValueError:
            customs = None
    return Candidate(
        item_id=_text(record["item_id"]),
        platform=platform,  # type: ignore[arg-type]
        marketplace=platform,  # type: ignore[arg-type]
        offer_id=_text(record.get("offer_id")) or None,
        title=_text(record["title"]),
        price=float(record["price"]),
        currency=_text(record["currency"]).upper(),
        rating=_float(record.get("rating")),
        sales=_int(record.get("sales")),
        image_url=_text(record.get("image_url")) or None,
        product_url=_text(record.get("product_url")) or None,
        link_kind="product_detail" if record.get("product_url") else "marketplace_search",
        attributes=attributes,
        identity=identity,
        variant_attributes=_mapping(record.get("variant_attributes")),
        availability=_text(record.get("availability")) or None,
        retrieved_at=_timestamp(record.get("retrieved_at")),
        provenance=provenance,
        identity_evidence=IdentityEvidence(),
        source=source,  # type: ignore[arg-type]
        price_conversion=price_conversion,
        shipping_quote=shipping_quote,
        customs=customs,
    )


__all__ = ["candidate_from_product_record", "normalize_product_record"]
