from __future__ import annotations

import hashlib
import os
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.data.product_faiss import ProductCatalogError, get_product_catalog
from app.provider_resilience import ProviderCircuitOpenError, get_provider_resilience
from app.schemas import (
    Candidate,
    CurrencyConversionEvidence,
    CustomsExchangeRateEvidence,
    CustomsTaxEvidence,
    CustomsValuationEvidence,
    ItemSearchOutput,
    OfferProvenance,
    Platform,
    ProviderFailureReason,
    ProviderMetadata,
    ShippingQuoteEvidence,
)
from app.tools import ebay_browse
from app.tools.marketplace_gateway import normalize_gateway_response
from app.tools.product_search import (
    ProductSearchError,
    ProductSearchFilters,
    search_products,
)
from app.tools.query_parser import extract_budget_cny, extract_product_subject


def _snapshot_manifest_covers_query(query: str, platform: Platform) -> bool:
    """Avoid returning semantically unrelated rows from a narrow eBay snapshot."""

    if platform != "ebay":
        return True
    import json
    from pathlib import Path

    path = Path(os.getenv("EBAY_SNAPSHOT_MANIFEST", "data/ebay_snapshots/browse-manifest.json"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    if not path.is_file():
        return True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    covered = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(covered, list) or not covered:
        return True
    subject = extract_product_subject(query)
    query_terms = {term.casefold() for term in query.split() if len(term.strip()) >= 2}
    query_terms.update(term.casefold() for term in subject.split() if len(term.strip()) >= 2)
    query_terms.update(
        term.casefold()
        for term in _LOCAL_CATALOG_QUERY_ALIASES.get(subject, "").split()
        if len(term.strip()) >= 2
    )
    if not query_terms:
        return True
    return any(
        query_terms.intersection(str(item).casefold().split())
        for item in covered
        if isinstance(item, str)
    )


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

_LOCAL_CATALOG_QUERY_ALIASES = {
    "耳机": "headphones",
    "降噪": "noise cancelling",
    "充电器": "charger",
    "笔记本": "laptop",
    "电脑": "computer",
    "相机": "camera",
    "键盘": "keyboard",
    "鼠标": "mouse",
    "显示器": "monitor",
    "手机": "cell phone smartphone",
    "音箱": "speaker",
    "路由器": "router",
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

_SANDBOX_HS_CODES = {
    "耳机": "8518300000",
    "咖啡机": "8516713000",
    "背包": "4202920000",
    "键盘": "8471607100",
    "运动鞋": "6404110000",
    "手机": "8517130000",
    "商品": "0000000000",
}

_SANDBOX_SHIPPING = {
    "amazon": (85.0, "US", "Sandbox international priority", 8, 12),
    "shopee": (35.0, "SG", "Sandbox cross-border standard", 7, 10),
    "aliexpress": (20.0, "CN", "Sandbox consolidated delivery", 18, 25),
    "ebay": (90.0, "US", "Sandbox international tracked", 10, 14),
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
        price_conversion = CurrencyConversionEvidence(
            source_currency=info["currency"],
            rate_to_cny=fx,
            rate_type="sandbox_fixture",
            markup_status="excluded",
            provider="deterministic-sandbox-fx-fixture",
            source_reference="sandbox fixture; not a payment or settlement rate",
            observed_at="2026-01-01T00:00:00Z",
        )
        shipping_cny, origin_country, service_name, eta_min, eta_max = _SANDBOX_SHIPPING[platform]
        shipping_amount = round(shipping_cny / fx, 2)
        weight = float(attributes.get("weight_kg", 0.5))
        price_amount = round(base_cny * multiplier / fx, 2)
        goods_value_cny = round(price_amount * fx, 2)
        items.append(
            Candidate(
                item_id=item_id,
                platform=platform,
                title=f"{info['label']} {variant}{subject}",
                price=price_amount,
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
                price_conversion=price_conversion,
                shipping_quote=ShippingQuoteEvidence(
                    quote_type="sandbox_fixture",
                    currency=info["currency"],
                    total_amount=shipping_amount,
                    base_amount=shipping_amount,
                    actual_weight_kg=weight,
                    chargeable_weight_kg=weight,
                    origin_country=origin_country,
                    destination_country="CN",
                    service_name=service_name,
                    eta_min_days=eta_min,
                    eta_max_days=eta_max,
                    provider="deterministic-sandbox-shipping-fixture",
                    source_reference="sandbox fixture; not a carrier or checkout quote",
                    observed_at="2026-01-01T00:00:00Z",
                    currency_conversion=CurrencyConversionEvidence(
                        source_currency=info["currency"],
                        rate_to_cny=fx,
                        rate_type="sandbox_fixture",
                        markup_status="excluded",
                        provider="deterministic-sandbox-fx-fixture",
                        source_reference="sandbox shipping conversion fixture",
                        observed_at="2026-01-01T00:00:00Z",
                    ),
                ),
                customs=CustomsTaxEvidence(
                    hs_code=_SANDBOX_HS_CODES.get(subject, "0000000000"),
                    country_of_origin="CN",
                    destination_country="CN",
                    import_regime="cross_border_ecommerce",
                    rate_type="cross_border_policy",
                    tariff_rate=0,
                    import_vat_rate=0.13,
                    consumption_tax_rate=0,
                    valuation=CustomsValuationEvidence(
                        goods_value_original=price_amount,
                        goods_currency=info["currency"],
                        goods_value_cny=goods_value_cny,
                        international_shipping_cny=shipping_cny,
                        insurance_cny=0,
                        customs_value_cny=round(goods_value_cny + shipping_cny, 2),
                        customs_conversion=CustomsExchangeRateEvidence(
                            source_currency=info["currency"],
                            rate_to_cny=fx,
                            declaration_date="2026-01-15",
                            assessment_month="2026-01",
                            provider="deterministic-sandbox-customs-fx-fixture",
                            source_reference=(
                                "sandbox monthly customs FX fixture; not an official customs rate"
                            ),
                        ),
                        provider="deterministic-sandbox-customs-valuation-fixture",
                        source_reference="sandbox CIF fixture; not a customs assessment",
                    ),
                    cross_border_ecommerce_eligible=True,
                    provider="deterministic-sandbox-tax-fixture",
                    source_reference="sandbox fixture; not a legal tariff classification",
                    effective_date="2026-01-01",
                ),
                link_kind="marketplace_search",
                source="fixture",
            )
        )
    return items[:top_k]


def _parse_live_item(raw: dict[str, object], platform: Platform) -> Candidate | None:
    candidates = normalize_gateway_response([raw], platform)
    return candidates[0] if candidates else None


async def _live_search_once(query: str, platform: Platform, top_k: int) -> list[Candidate]:
    settings = get_settings()
    marketplace = next(item for item in settings.marketplaces if item.name == platform)
    endpoint = marketplace.endpoint
    credential = marketplace.credential
    headers = {"Authorization": f"Bearer {credential}", "X-API-Key": credential}
    transport = httpx.AsyncHTTPTransport(retries=0)
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


async def _live_search(query: str, platform: Platform, top_k: int) -> list[Candidate]:
    async def operation() -> list[Candidate]:
        candidates = await _live_search_once(query, platform, top_k)
        if not candidates:
            raise LookupError("provider returned no valid products")
        return candidates

    return await get_provider_resilience().execute(platform, operation)


def _ensure_channel_provenance(candidate: Candidate, provider: str) -> Candidate:
    provenance = candidate.provenance
    if provenance is None:
        provenance = OfferProvenance(kind="marketplace_gateway", provider=provider)
    elif provenance.provider is None:
        provenance = provenance.model_copy(update={"provider": provider})
    return candidate.model_copy(update={"provenance": provenance})


async def _opensearch_product_search(
    query: str, platform: Platform, top_k: int
) -> ItemSearchOutput:
    settings = get_settings()
    budget_cny = extract_budget_cny(query)
    result = await search_products(
        query,
        platform,
        top_k=top_k,
        settings=settings,
        filters=ProductSearchFilters(
            platform=platform,
            marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "").strip()
            if platform == "ebay"
            else None,
            # A landed cost can never be lower than the product price, so this
            # is a safe coarse filter when the source materializes price_cny.
            # Historical Amazon metadata may lack an offer-level quote; keep those rows visible
            # so the main comparison stage can attach the current reference FX quote.
            price_max_cny=(
                None
                if platform == "amazon" and settings.product_search_allow_historical
                else budget_cny
            ),
            sources=("live", "fixture")
            if settings.sandbox_mode
            or (platform == "amazon" and settings.product_search_allow_historical)
            else ("live",),
            # Snapshot expiry is retained as provenance but is intentionally not a recall gate.
            # The caller requested ANN retrieval over the available product corpus first.
            require_fresh=False,
        ),
    )
    if not result.candidates:
        raise ProductSearchError("OpenSearch product search returned no valid products")
    source = (
        "fixture"
        if all(candidate.source == "fixture" for candidate in result.candidates)
        else "live"
    )
    status = "degraded" if result.degradation_reason else "ok"
    return ItemSearchOutput(
        platform=platform,
        candidates=result.candidates,
        total_recall=result.total_hits or len(result.candidates),
        truncated=len(result.candidates) >= top_k,
        ann_used=result.vector_used,
        provider=ProviderMetadata(
            source=source,  # type: ignore[arg-type]
            provider="opensearch-product-index",
            status=status,  # type: ignore[arg-type]
            fallback_reason=result.degradation_reason
            or ("历史快照商品数据" if source == "fixture" else None),
        ),
    )


async def _official_ebay_search(
    query: str, top_k: int, *, fallback_reason: str
) -> ItemSearchOutput:
    candidates = await ebay_browse.search(query, top_k=top_k)
    if not candidates:
        raise ebay_browse.EbayBrowseError("eBay Browse returned no valid products")
    return ItemSearchOutput(
        platform="ebay",
        candidates=candidates,
        total_recall=len(candidates),
        truncated=len(candidates) >= top_k,
        provider=ProviderMetadata(
            source="live",
            provider="ebay-buy-browse-live",
            status="degraded",
            fallback_reason=fallback_reason,
        ),
    )


def _local_catalog_candidates(query: str, platform: Platform, top_k: int) -> list[Candidate]:
    settings = get_settings()
    if (
        settings.product_catalog_backend != "faiss"
        or platform != "amazon"
        or settings.app_env == "production"
    ):
        return []
    try:
        expanded_query = " ".join(
            [query]
            + [alias for token, alias in _LOCAL_CATALOG_QUERY_ALIASES.items() if token in query]
        )
        records = get_product_catalog(settings.product_catalog_index_dir).search(
            expanded_query,
            top_k=min(top_k, 5),
            min_score=0.35,
        )
    except ProductCatalogError:
        return []
    # Public historical catalog data has no current route/tax evidence. Reuse only the explicit
    # Sandbox cost fixtures and disclose that boundary on every returned candidate.
    cost_templates = _fixture_candidates(query, platform, min(top_k, 3))
    if not cost_templates:
        return []
    candidates: list[Candidate] = []
    for index, record in enumerate(records):
        template = cost_templates[index % len(cost_templates)]
        asin = str(record["asin"])
        attributes = {
            **template.attributes,
            "brand": record.get("brand"),
            "category_path": record.get("categories", []),
            "description": record.get("description", ""),
            "historical_catalog": True,
            "catalog_snapshot": "Amazon Electronics metadata (2015)",
            "faiss_score": record.get("faiss_score"),
        }
        candidates.append(
            template.model_copy(
                update={
                    "item_id": f"amazon:{asin}",
                    "offer_id": asin,
                    "title": str(record["title"]),
                    "price": float(record["price"]),
                    "currency": str(record.get("currency") or "USD"),
                    "image_url": record.get("image_url"),
                    "product_url": f"https://www.amazon.com/dp/{asin}",
                    "attributes": attributes,
                    "variant_attributes": {},
                    "retrieved_at": "2015-09-24T00:00:00Z",
                    "source": "fixture",
                    "provenance": OfferProvenance(
                        kind="sandbox_fixture",
                        provider="amazon-public-electronics-catalog",
                        upstream_source=(
                            "UCSD/SNAP Amazon Electronics metadata; historical catalog, not live"
                        ),
                    ),
                    "link_kind": "product_detail",
                }
            )
        )
    return candidates


async def item_search(
    query: str,
    platform: Platform,
    top_k: int = 20,
    user_id: str | None = None,
) -> ItemSearchOutput:
    """Search one marketplace and disclose unavailable or fixture-backed results."""

    del user_id  # Kept for API compatibility; the default dual-tower path has no User Tower.
    top_k = max(1, min(top_k, 50))
    settings = get_settings()
    endpoint_env, credential_env, legacy_endpoint_env, legacy_credential_env = (
        _CHANNEL_CONFIG_NAMES[platform]
    )
    marketplace = next(item for item in settings.marketplaces if item.name == platform)
    configured = marketplace.configured
    product_search_enabled = (
        settings.product_search_backend == "opensearch"
        and platform in settings.product_search_marketplaces
    )
    if product_search_enabled:
        if (
            platform == "ebay"
            and not settings.sandbox_mode
            and not _snapshot_manifest_covers_query(query, platform)
            and ebay_browse.configured()
        ):
            try:
                return await _official_ebay_search(
                    query,
                    top_k,
                    fallback_reason="当前离线快照不覆盖该关键词，已调用 eBay Browse 实时搜索",
                )
            except ebay_browse.EbayBrowseError:
                pass
        try:
            return await _opensearch_product_search(query, platform, top_k)
        except ProductSearchError as exc:
            if platform == "ebay" and not settings.sandbox_mode and ebay_browse.configured():
                try:
                    return await _official_ebay_search(
                        query,
                        top_k,
                        fallback_reason=(
                            f"OpenSearch product snapshot unavailable; direct Browse fallback: {exc}"
                        ),
                    )
                except ebay_browse.EbayBrowseError:
                    pass
            if not settings.sandbox_mode:
                return ItemSearchOutput(
                    platform=platform,
                    candidates=[],
                    total_recall=0,
                    truncated=False,
                    provider=ProviderMetadata(
                        source="live",
                        provider="opensearch-product-index",
                        status="unavailable",
                        fallback_reason=str(exc),
                        failure_reason=(
                            "empty_response"
                            if "no valid products" in str(exc)
                            else "request_failed"
                        ),
                    ),
                )
    if (
        platform == "ebay"
        and not settings.sandbox_mode
        and ebay_browse.configured()
        and not product_search_enabled
    ):
        try:
            return await _official_ebay_search(
                query,
                top_k,
                fallback_reason="OpenSearch product search is disabled; direct Browse path is active",
            )
        except ebay_browse.EbayBrowseError:
            pass
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
        candidates = _local_catalog_candidates(query, platform, top_k)
        if candidates:
            return ItemSearchOutput(
                platform=platform,
                candidates=candidates,
                total_recall=len(candidates),
                truncated=len(candidates) >= top_k,
                provider=ProviderMetadata(
                    source="fixture",
                    provider="amazon-public-electronics-catalog",
                    status="degraded",
                    fallback_reason=(
                        "历史公开商品目录已启用；商品信息来自目录快照，价格/运费/税费使用 Sandbox 估算"
                    ),
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
        except ProviderCircuitOpenError as exc:
            return ItemSearchOutput(
                platform=platform,
                candidates=[],
                total_recall=0,
                truncated=False,
                provider=ProviderMetadata(
                    source="live",
                    provider=marketplace.provider,
                    status="unavailable",
                    fallback_reason=str(exc),
                    failure_reason="circuit_open",
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
