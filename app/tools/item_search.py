from __future__ import annotations

import hashlib
import math
import os
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.schemas import Candidate, ItemSearchOutput, Platform, ProviderMetadata
from app.tools.query_parser import extract_budget_cny, extract_product_subject

_PLATFORM_CONFIG = {
    "amazon": ("AMAZON_API_ENDPOINT", "AMAZON_API_KEY"),
    "shopee": ("SHOPEE_API_ENDPOINT", "SHOPEE_API_KEY"),
    "aliexpress": ("ALIEXPRESS_API_ENDPOINT", "ALIEXPRESS_API_KEY"),
    "ebay": ("EBAY_API_ENDPOINT", "EBAY_API_KEY"),
}

_PLATFORM_INFO = {
    "amazon": {
        "label": "Amazon",
        "currency": "USD",
        "factor": 1.0,
        "search": "https://www.amazon.com/s?k=",
    },
    "shopee": {
        "label": "Shopee",
        "currency": "SGD",
        "factor": 0.92,
        "search": "https://shopee.sg/search?keyword=",
    },
    "aliexpress": {
        "label": "AliExpress",
        "currency": "USD",
        "factor": 0.82,
        "search": "https://www.aliexpress.com/wholesale?SearchText=",
    },
    "ebay": {
        "label": "eBay",
        "currency": "USD",
        "factor": 1.08,
        "search": "https://www.ebay.com/sch/i.html?_nkw=",
    },
}

_CATEGORY_BASE_CNY = {
    "耳机": 760,
    "咖啡机": 1550,
    "背包": 520,
    "键盘": 680,
    "运动鞋": 620,
    "手机": 4200,
    "商品": 800,
}

_CATEGORY_IMAGES = {
    "耳机": [
        "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1484704849700-f032a568e944?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1606220588913-b3aacb4d2f46?auto=format&fit=crop&w=900&q=80",
    ],
    "咖啡机": [
        "https://images.unsplash.com/photo-1517914309068-900c27e49df1?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1570087935869-1e676d5fca13?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?auto=format&fit=crop&w=900&q=80",
    ],
    "背包": [
        "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1622560480605-d83c853bc5c3?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1581605405669-fcdf81165afa?auto=format&fit=crop&w=900&q=80",
    ],
    "键盘": [
        "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1595044426077-d36d9236d54a?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1618384887929-16ec33fab9ef?auto=format&fit=crop&w=900&q=80",
    ],
    "运动鞋": [
        "https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1549298916-b41d501d3772?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1460353581641-37baddab0fa2?auto=format&fit=crop&w=900&q=80",
    ],
    "手机": [
        "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1592899677977-9c10ca588bbd?auto=format&fit=crop&w=900&q=80",
        "https://images.unsplash.com/photo-1605236453806-6ff36851218e?auto=format&fit=crop&w=900&q=80",
    ],
}


_DEFAULT_VARIANTS = (
    ("高性价比", 0.88, {"weight_kg": 0.34, "material": "织物", "style": "简约"}, 4.6, 2380),
    ("均衡进阶", 1.03, {"weight_kg": 0.48, "material": "金属与织物", "style": "通勤"}, 4.7, 1620),
    ("高配方案", 1.22, {"weight_kg": 0.62, "material": "铝合金", "style": "专业"}, 4.8, 890),
)

_PHONE_VARIANTS = (
    (
        "易用大字款",
        0.82,
        {"weight_kg": 0.19, "storage": "128 GB", "display": "6.5 英寸"},
        4.5,
        3100,
    ),
    (
        "均衡长续航款",
        0.97,
        {"weight_kg": 0.2, "storage": "256 GB", "display": "6.7 英寸"},
        4.7,
        2450,
    ),
    (
        "高配影像款",
        1.14,
        {"weight_kg": 0.21, "storage": "512 GB", "display": "6.7 英寸"},
        4.8,
        1280,
    ),
)

_UNKNOWN_VARIANTS = (
    ("高性价比方案", 0.88, {}, 4.6, 2380),
    ("均衡进阶方案", 1.03, {}, 4.7, 1620),
    ("高配方案", 1.22, {}, 4.8, 890),
)


def _fixture_candidates(query: str, platform: Platform, top_k: int) -> list[Candidate]:
    subject = extract_product_subject(query)
    known_template = subject in _CATEGORY_BASE_CNY and subject != "商品"
    info = _PLATFORM_INFO[platform]
    budget = extract_budget_cny(query)
    reference_cny = budget * 0.9 if budget is not None else _CATEGORY_BASE_CNY.get(subject, 800)
    base_cny = reference_cny * info["factor"]
    fx = {"USD": 7.18, "SGD": 5.32}[info["currency"]]
    images = _CATEGORY_IMAGES.get(subject, [None, None, None])
    variants = (
        _PHONE_VARIANTS
        if subject == "手机"
        else _DEFAULT_VARIANTS
        if known_template
        else _UNKNOWN_VARIANTS
    )
    subject_hash = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:10]
    items: list[Candidate] = []
    for index, (variant, multiplier, attributes, rating, sales) in enumerate(variants):
        item_id = f"fixture-{platform}-{subject_hash}-{index + 1}"
        items.append(
            Candidate(
                item_id=item_id,
                platform=platform,
                title=f"{info['label']} {variant}{subject}",
                price=round(base_cny * multiplier / fx, 2),
                currency=info["currency"],
                rating=rating,
                sales=sales,
                image_url=images[index],
                product_url=f"{info['search']}{quote_plus(subject)}",
                attributes={**attributes, "sandbox": True},
                source="fixture",
            )
        )
    return items[:top_k]


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return None


def _safe_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.startswith(("https://", "http://")):
        return stripped
    return None


def _parse_live_item(raw: dict[str, Any], platform: Platform) -> Candidate | None:
    title = _first(raw, "title", "name", "product_name")
    price = _first(raw, "price", "current_price", "sale_price")
    currency = _first(raw, "currency", "currency_code")
    if title is None or price is None or currency is None:
        return None
    try:
        numeric_price = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_price) or numeric_price < 0:
        return None
    product_url = _safe_http_url(_first(raw, "product_url", "url", "link"))
    source_id = _first(raw, "item_id", "id", "product_id", "sku")
    if source_id is None:
        identity = f"{platform}|{title}|{product_url or numeric_price}".encode()
        source_id = hashlib.sha256(identity).hexdigest()[:20]
    rating_raw = _first(raw, "rating", "score")
    sales_raw = _first(raw, "sales", "sold", "sales_count")
    return Candidate(
        item_id=str(source_id),
        platform=platform,
        title=str(title),
        price=numeric_price,
        currency=str(currency).upper(),
        rating=_optional_float(rating_raw),
        sales=_optional_int(sales_raw),
        image_url=_safe_http_url(_first(raw, "image_url", "image", "thumbnail")),
        product_url=product_url,
        attributes=raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {},
        source="live",
    )


async def _live_search(query: str, platform: Platform, top_k: int) -> list[Candidate]:
    endpoint_env, key_env = _PLATFORM_CONFIG[platform]
    endpoint = os.environ[endpoint_env]
    api_key = os.environ[key_env]
    headers = {"Authorization": f"Bearer {api_key}", "X-API-Key": api_key}
    transport = httpx.AsyncHTTPTransport(retries=2)
    async with httpx.AsyncClient(
        timeout=get_settings().provider_timeout_seconds,
        transport=transport,
    ) as client:
        response = await client.get(
            endpoint, params={"query": query, "top_k": top_k}, headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    if isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = payload.get("items") or payload.get("products") or payload.get("data") or []
        if isinstance(raw_items, dict):
            raw_items = raw_items.get("items") or raw_items.get("products") or []
    parsed = [_parse_live_item(item, platform) for item in raw_items if isinstance(item, dict)]
    return [item for item in parsed if item is not None][:top_k]


async def item_search(
    query: str,
    platform: Platform,
    top_k: int = 20,
    user_id: str | None = None,
) -> ItemSearchOutput:
    """Search one marketplace and disclose unavailable or fixture-backed results."""

    del user_id  # Reserved for the three-tower personalized recall channel.
    top_k = max(1, min(top_k, 50))
    settings = get_settings()
    endpoint_env, key_env = _PLATFORM_CONFIG[platform]
    configured = bool(os.getenv(endpoint_env) and os.getenv(key_env))
    if settings.sandbox_mode:
        candidates = _fixture_candidates(query, platform, top_k)
        return ItemSearchOutput(
            platform=platform,
            candidates=candidates,
            total_recall=len(candidates),
            truncated=False,
            provider=ProviderMetadata(
                source="fixture",
                provider=f"{platform}-sandbox",
                status="degraded",
                fallback_reason="已显式启用沙盒模式",
            ),
        )

    if configured:
        try:
            candidates = await _live_search(query, platform, top_k)
            if not candidates:
                raise LookupError("provider returned no valid products")
            return ItemSearchOutput(
                platform=platform,
                candidates=candidates,
                total_recall=len(candidates),
                truncated=len(candidates) >= top_k,
                provider=ProviderMetadata(source="live", provider=platform),
            )
        except Exception as exc:  # noqa: BLE001 - provider failures become typed metadata
            reason = f"provider request failed: {type(exc).__name__}"
            if settings.fixture_fallback_enabled:
                candidates = _fixture_candidates(query, platform, top_k)
                return ItemSearchOutput(
                    platform=platform,
                    candidates=candidates,
                    total_recall=len(candidates),
                    truncated=False,
                    provider=ProviderMetadata(
                        source="fixture",
                        provider=f"{platform}-sandbox",
                        status="degraded",
                        fallback_reason=reason,
                    ),
                )
            return ItemSearchOutput(
                platform=platform,
                candidates=[],
                total_recall=0,
                truncated=False,
                provider=ProviderMetadata(
                    source="live",
                    provider=platform,
                    status="unavailable",
                    fallback_reason=reason,
                ),
            )

    return ItemSearchOutput(
        platform=platform,
        candidates=[],
        total_recall=0,
        truncated=False,
        provider=ProviderMetadata(
            source="live",
            provider=platform,
            status="unavailable",
            fallback_reason=f"{endpoint_env} and {key_env} are not fully configured",
        ),
    )
