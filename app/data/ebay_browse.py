"""Shared eBay Browse response projection for snapshots and live fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.schemas import CurrencyConversionEvidence


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def amount(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def browse_context_header(country: str, postal_code: str) -> str:
    context = f"country={country.upper()},zip={postal_code}"
    return f"contextualLocation={quote(context, safe='')}"


def delivery_filter(country: str, postal_code: str) -> str:
    return f"deliveryCountry:{country.upper()},deliveryPostalCode:{postal_code}"


def _timestamp(value: Any) -> datetime | None:
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _eta_days(value: Any, *, now: datetime) -> int | None:
    timestamp = _timestamp(value)
    if timestamp is None:
        return None
    return max(0, (timestamp.date() - now.date()).days)


def _specifics(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("localizedAspects") or item.get("itemSpecifics")
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items()}
    if not isinstance(raw, list):
        return {}
    values: dict[str, Any] = {}
    for aspect in raw:
        if not isinstance(aspect, dict):
            continue
        name = text(aspect.get("name"))
        value = aspect.get("value")
        if name and value is not None:
            values[name] = value
    return values


def _specific_value(specifics: dict[str, Any], *names: str) -> str | None:
    normalized = {key.casefold(): value for key, value in specifics.items()}
    for name in names:
        value = normalized.get(name.casefold())
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value and isinstance(value[0], str) and value[0].strip():
            return value[0].strip()
    return None


def _shipping_quote(
    item: dict[str, Any],
    *,
    item_id: str,
    marketplace_id: str,
    snapshot_at: str,
    expires_at: str,
    destination_country: str,
    destination_postal_code: str,
    conversions: dict[str, CurrencyConversionEvidence],
) -> dict[str, Any] | None:
    options = item.get("shippingOptions")
    if not isinstance(options, list):
        return None
    now = _timestamp(snapshot_at) or datetime.now(timezone.utc)
    for option in options:
        if not isinstance(option, dict):
            continue
        shipping = option.get("shippingCost")
        if not isinstance(shipping, dict):
            continue
        total = amount(shipping.get("value"))
        currency = text(shipping.get("currency")).upper()
        eta_min = _eta_days(option.get("minEstimatedDeliveryDate"), now=now)
        eta_max = _eta_days(option.get("maxEstimatedDeliveryDate"), now=now)
        origin = text((item.get("itemLocation") or {}).get("country"))
        if total is None or not currency or not origin or eta_min is None or eta_max is None:
            continue
        conversion = conversions.get(currency)
        if currency != "CNY" and conversion is None:
            continue
        return {
            "quote_type": "marketplace_estimate",
            "currency": currency,
            "total_amount": total,
            "base_amount": total,
            "surcharge_amount": 0,
            "discount_amount": 0,
            "origin_country": origin,
            "destination_country": destination_country.upper(),
            "service_name": text(option.get("shippingServiceCode"))
            or f"eBay {text(option.get('shippingCostType')) or 'shipping'} estimate",
            "eta_min_days": eta_min,
            "eta_max_days": eta_max,
            "provider": "ebay-buy-browse",
            "source_reference": f"ebay:{item_id}:shipping:{marketplace_id}:{destination_postal_code}",
            "observed_at": snapshot_at,
            "expires_at": expires_at,
            "currency_conversion": conversion.model_dump(mode="json") if conversion else None,
        }
    return None


def browse_item_to_record(
    item: dict[str, Any],
    *,
    marketplace_id: str,
    snapshot_at: str,
    expires_at: str,
    destination_country: str = "CN",
    destination_postal_code: str = "100000",
    conversions: dict[str, CurrencyConversionEvidence] | None = None,
    source: str = "live",
    provider: str = "ebay-buy-browse-snapshot",
) -> dict[str, Any] | None:
    item_id = text(item.get("itemId"))
    title = text(item.get("title"))
    price = item.get("price") if isinstance(item.get("price"), dict) else {}
    value = amount(price.get("value"))
    currency = text(price.get("currency")).upper()
    if not item_id or not title or value is None or not currency:
        return None
    categories = item.get("categories") if isinstance(item.get("categories"), list) else []
    category_ids = [
        text(category.get("categoryId"))
        for category in categories
        if isinstance(category, dict) and text(category.get("categoryId"))
    ]
    category_names = [
        text(category.get("categoryName"))
        for category in categories
        if isinstance(category, dict) and text(category.get("categoryName"))
    ]
    image = item.get("image") if isinstance(item.get("image"), dict) else {}
    specifics = _specifics(item)
    hs_code = _specific_value(specifics, "HS Code", "HS Code (Harmonized System)", "海关编码")
    country_of_origin = _specific_value(
        specifics, "Country of Origin", "Origin Country", "原产地", "产地"
    )
    end_at = _timestamp(item.get("itemEndDate"))
    is_active = end_at is None or end_at > datetime.now(timezone.utc)
    conversion = (conversions or {}).get(currency)
    attributes: dict[str, Any] = {
        "condition": text(item.get("condition")) or None,
        "buying_options": item.get("buyingOptions") or [],
        "item_location": item.get("itemLocation") or {},
        "seller": item.get("seller") or {},
        "localized_aspects": specifics,
        "listing_marketplace_id": text(item.get("listingMarketplaceId")) or marketplace_id,
    }
    if price.get("convertedFromValue") is not None:
        attributes["converted_from_price"] = {
            "value": price.get("convertedFromValue"),
            "currency": text(price.get("convertedFromCurrency")).upper() or None,
        }
    shipping_quote = _shipping_quote(
        item,
        item_id=item_id,
        marketplace_id=marketplace_id,
        snapshot_at=snapshot_at,
        expires_at=expires_at,
        destination_country=destination_country,
        destination_postal_code=destination_postal_code,
        conversions=conversions or {},
    )
    record: dict[str, Any] = {
        "item_id": f"ebay:{item_id}",
        "offer_id": item_id,
        "platform": "ebay",
        "marketplace_id": marketplace_id,
        "title": title,
        "brand": specifics.get("Brand") or specifics.get("品牌"),
        "categories": category_names,
        "category_ids": category_ids or [text(item.get("leafCategoryIds", [""])[0])]
        if isinstance(item.get("leafCategoryIds"), list) and item.get("leafCategoryIds")
        else category_ids,
        "description": text(item.get("shortDescription")),
        "price": value,
        "currency": currency,
        "image_url": text(image.get("imageUrl")) or None,
        "product_url": text(item.get("itemAffiliateWebUrl"))
        or text(item.get("itemWebUrl"))
        or None,
        "availability": text(item.get("estimatedAvailabilityStatus")) or "available",
        "condition": text(item.get("condition")) or None,
        "is_active": is_active,
        "source": source,
        "provider": provider,
        "upstream_source": "ebay-buy-browse",
        "snapshot_at": snapshot_at,
        "expires_at": expires_at,
        "retrieved_at": snapshot_at,
        "gtin": text(item.get("gtin")) or None,
        "hs_code": hs_code,
        "country_of_origin": country_of_origin,
        "ship_from_country": text((item.get("itemLocation") or {}).get("country")) or None,
        "attributes": attributes,
        "keywords": [*category_names, *specifics.keys()],
        "shipping_quote": shipping_quote,
    }
    if conversion is not None:
        record["price_conversion"] = conversion.model_dump(mode="json")
    return {key: value for key, value in record.items() if value is not None}


__all__ = ["browse_context_header", "browse_item_to_record", "delivery_filter"]
