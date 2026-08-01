from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.schemas import Candidate, OfferProvenance, Platform, ProductIdentity

_ITEM_WRAPPERS = ("items", "products", "results", "offers")


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if any(ord(character) < 32 for character in stripped):
        return None
    try:
        parsed = urlsplit(stripped)
        hostname = parsed.hostname
        port = parsed.port  # Accessing port validates both syntax and range.
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or (port is not None and not 0 <= port <= 65535)
    ):
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return stripped


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _availability(value: Any) -> str | None:
    if isinstance(value, bool):
        return "in_stock" if value else "out_of_stock"
    text = _text(value)
    if text is None:
        return None
    normalized = text.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "available": "in_stock",
        "instock": "in_stock",
        "sold_out": "out_of_stock",
        "unavailable": "out_of_stock",
        "pre_order": "preorder",
    }
    normalized = aliases.get(normalized, normalized)
    return (
        normalized
        if normalized in {"in_stock", "out_of_stock", "limited", "preorder", "backorder"}
        else None
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _optional_float(value: Any) -> float | None:
    number = _number(value)
    return number


def _link(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    kind = _text(_first(raw, "link_kind", "url_kind"))
    normalized_kind = kind.lower() if kind is not None else None
    detail_url = _safe_http_url(
        _first(raw, "product_url", "detail_url", "item_web_url", "url", "link")
    )
    search_url = _safe_http_url(_first(raw, "search_url", "marketplace_search_url"))
    if normalized_kind == "marketplace_search":
        return search_url, "marketplace_search" if search_url else None
    if normalized_kind == "product_detail":
        return detail_url, "product_detail" if detail_url else None
    if detail_url:
        return detail_url, "product_detail"
    if search_url:
        return search_url, "marketplace_search"
    return None, None


def _items(payload: Any, *, _depth: int = 0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], {}
    if not isinstance(payload, dict) or _depth > 4:
        return [], {}
    for key in _ITEM_WRAPPERS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)], payload
        if isinstance(value, dict):
            nested_items, nested_envelope = _items(value, _depth=_depth + 1)
            if nested_items:
                return nested_items, {**payload, **nested_envelope}
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], payload
    if isinstance(data, dict):
        nested_items, nested_envelope = _items(data, _depth=_depth + 1)
        if nested_items:
            return nested_items, {**payload, **nested_envelope}
    return [], payload


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_valid_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, bool)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _finite_json(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_json(item) for item in value]
    return value


def _identity(raw: dict[str, Any]) -> tuple[ProductIdentity, dict[str, Any]]:
    identity = _mapping(raw.get("identity"))
    gtin = _text(_first(identity, "gtin", "ean", "upc", "isbn")) or _text(
        _first(raw, "gtin", "ean", "upc", "isbn")
    )
    mpn = _text(identity.get("mpn")) or _text(raw.get("mpn"))
    brand = _text(identity.get("brand")) or _text(raw.get("brand"))
    model = _text(identity.get("model")) or _text(_first(raw, "model", "model_number"))
    variant_raw = _first(identity, "variant_attributes", "variant")
    if not isinstance(variant_raw, dict):
        variant_raw = _first(raw, "variant_attributes", "variant", "attributes")
    variant = {
        str(key): value for key, value in _mapping(variant_raw).items() if _is_valid_scalar(value)
    }
    return ProductIdentity(gtin=gtin, mpn=mpn, brand=brand, model=model), variant


def _provenance(raw: dict[str, Any], envelope: dict[str, Any]) -> OfferProvenance | None:
    envelope_provenance = _mapping(envelope.get("provenance"))
    item_provenance = _mapping(raw.get("provenance"))
    provider = (
        _text(item_provenance.get("provider"))
        or _text(raw.get("provider"))
        or _text(envelope_provenance.get("provider"))
        or _text(envelope.get("provider"))
    )
    upstream_source = (
        _text(_first(item_provenance, "upstream_source", "source"))
        or _text(_first(raw, "upstream_source", "source"))
        or _text(_first(envelope_provenance, "upstream_source", "source"))
        or _text(_first(envelope, "upstream_source", "source"))
    )
    if provider is None and upstream_source is None:
        return None
    return OfferProvenance(
        kind="marketplace_gateway",
        provider=provider,
        upstream_source=upstream_source,
    )


def normalize_gateway_response(payload: Any, platform: Platform) -> list[Candidate]:
    """Normalize the supported Marketplace Gateway wrappers into Product Evidence."""

    raw_items, envelope = _items(payload)
    candidates: list[Candidate] = []
    for raw in raw_items:
        claimed_marketplace = _text(_first(raw, "marketplace", "platform"))
        if claimed_marketplace is not None and claimed_marketplace.lower() != platform:
            continue
        title = _text(_first(raw, "title", "name", "product_name"))
        price = _number(_first(raw, "price", "current_price", "sale_price"))
        currency = _text(_first(raw, "currency", "currency_code"))
        if title is None or price is None or currency is None:
            continue

        offer_id = _text(_first(raw, "offer_id", "item_id", "id", "product_id", "sku"))
        product_url, link_kind = _link(raw)
        item_id = offer_id
        if item_id is None:
            internal_identity = f"{platform}|{title}|{product_url or price}".encode()
            item_id = f"candidate-{hashlib.sha256(internal_identity).hexdigest()[:20]}"
        identity, variant_attributes = _identity(raw)
        retrieved_at = _timestamp(
            _first(raw, "retrieved_at", "observed_at", "fetched_at")
            or _first(envelope, "retrieved_at", "observed_at", "fetched_at")
        )
        attributes = _finite_json(_mapping(raw.get("attributes")))
        try:
            candidate = Candidate(
                item_id=item_id,
                platform=platform,
                marketplace=platform,
                offer_id=offer_id,
                title=title,
                price=price,
                currency=currency.upper(),
                rating=_optional_float(_first(raw, "rating", "score")),
                sales=_optional_int(_first(raw, "sales", "sold", "sales_count")),
                image_url=_safe_http_url(_first(raw, "image_url", "image", "thumbnail")),
                product_url=product_url,
                link_kind=link_kind,
                attributes=attributes,
                identity=identity,
                variant_attributes=variant_attributes,
                availability=_availability(
                    _first(raw, "availability", "availability_status", "stock_status", "in_stock")
                ),
                retrieved_at=retrieved_at,
                provenance=_provenance(raw, envelope),
                source="live",
            )
        except ValidationError:
            continue
        candidates.append(candidate)
    return candidates
