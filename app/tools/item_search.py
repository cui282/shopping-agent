from __future__ import annotations

import hashlib
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.schemas import (
    Candidate,
    ItemSearchOutput,
    OfferProvenance,
    Platform,
    ProviderFailureReason,
    ProviderMetadata,
)
from app.tools.marketplace_gateway import normalize_gateway_response
from app.tools.query_parser import extract_budget_cny, extract_product_subject

_CHANNEL_CONFIG_NAMES = {
    "amazon": (
        "AMAZON_DATA_CHANNEL_ENDPOINT",
        "AMAZON_DATA_CHANNEL_CREDENTIAL",
        "AMAZON_API_ENDPOINT",
        "AMAZON_API_KEY",
    ),
    "shopee": (
        "SHOPEE_DATA_CHANNEL_ENDPOINT",
        "SHOPEE_DATA_CHANNEL_CREDENTIAL",
        "SHOPEE_API_ENDPOINT",
        "SHOPEE_API_KEY",
    ),
    "aliexpress": (
        "ALIEXPRESS_DATA_CHANNEL_ENDPOINT",
        "ALIEXPRESS_DATA_CHANNEL_CREDENTIAL",
        "ALIEXPRESS_API_ENDPOINT",
        "ALIEXPRESS_API_KEY",
    ),
    "ebay": (
        "EBAY_DATA_CHANNEL_ENDPOINT",
        "EBAY_DATA_CHANNEL_CREDENTIAL",
        "EBAY_API_ENDPOINT",
        "EBAY_API_KEY",
    ),
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
                variant_attributes=attributes,
                provenance=OfferProvenance(
                    kind="sandbox_fixture",
                    provider=f"{platform}-sandbox",
                    upstream_source="deterministic-fixture-catalog",
                ),
                link_kind="marketplace_search",
                source="fixture",
            )
        )
    return items[:top_k]


def _parse_live_item(raw: dict[str, object], platform: Platform) -> Candidate | None:
    candidates = normalize_gateway_response([raw], platform)
    return candidates[0] if candidates else None


async def _live_search(query: str, platform: Platform, top_k: int) -> list[Candidate]:
    settings = get_settings()
    marketplace = next(item for item in settings.marketplaces if item.name == platform)
    endpoint = marketplace.endpoint
    credential = marketplace.credential
    headers = {"Authorization": f"Bearer {credential}", "X-API-Key": credential}
    transport = httpx.AsyncHTTPTransport(retries=2)
    async with httpx.AsyncClient(
        timeout=settings.provider_timeout_seconds,
        transport=transport,
    ) as client:
        response = await client.get(
            endpoint, params={"query": query, "top_k": top_k}, headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    candidates = normalize_gateway_response(payload, platform)[:top_k]
    return [_ensure_channel_provenance(candidate, marketplace.provider) for candidate in candidates]


def _ensure_channel_provenance(candidate: Candidate, provider: str) -> Candidate:
    provenance = candidate.provenance
    if provenance is None:
        provenance = OfferProvenance(kind="marketplace_gateway", provider=provider)
    elif provenance.provider is None:
        provenance = provenance.model_copy(update={"provider": provider})
    return candidate.model_copy(update={"provenance": provenance})


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
    endpoint_env, credential_env, legacy_endpoint_env, legacy_credential_env = (
        _CHANNEL_CONFIG_NAMES[platform]
    )
    marketplace = next(item for item in settings.marketplaces if item.name == platform)
    configured = marketplace.configured
    if settings.sandbox_mode:
        if settings.app_env == "production":
            return ItemSearchOutput(
                platform=platform,
                candidates=[],
                total_recall=0,
                truncated=False,
                provider=ProviderMetadata(
                    source="live",
                    provider=marketplace.provider,
                    status="unavailable",
                    fallback_reason="sandbox mode is forbidden in production",
                    failure_reason="sandbox_forbidden",
                ),
            )
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
                provider=ProviderMetadata(
                    source="live",
                    provider=(
                        candidates[0].provenance.provider
                        if candidates[0].provenance is not None
                        and candidates[0].provenance.provider is not None
                        else marketplace.provider
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - provider failures become typed metadata
            reason = f"provider request failed: {type(exc).__name__}"
            failure_reason: ProviderFailureReason = (
                "empty_response" if isinstance(exc, LookupError) else "request_failed"
            )
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
                        failure_reason=failure_reason,
                    ),
                )
            return ItemSearchOutput(
                platform=platform,
                candidates=[],
                total_recall=0,
                truncated=False,
                provider=ProviderMetadata(
                    source="live",
                    provider=marketplace.provider,
                    status="unavailable",
                    fallback_reason=reason,
                    failure_reason=failure_reason,
                ),
            )

    return ItemSearchOutput(
        platform=platform,
        candidates=[],
        total_recall=0,
        truncated=False,
        provider=ProviderMetadata(
            source="live",
            provider=marketplace.provider,
            status="unavailable",
            fallback_reason=(
                f"{endpoint_env} and {credential_env} are not fully configured; "
                f"legacy aliases {legacy_endpoint_env} and {legacy_credential_env} are supported"
            ),
            failure_reason="not_configured",
        ),
    )
