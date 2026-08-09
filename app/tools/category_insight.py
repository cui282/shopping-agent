from __future__ import annotations

import os
from typing import Any, Literal

import httpx

from app.config import get_settings
from app.provider_resilience import ProviderCircuitOpenError, get_provider_resilience
from app.recall.category_norm import normalize_category
from app.recall.category_reranker import HTTPTextReranker
from app.recall.rag import extract_structured_card, summarize_card
from app.recall.tower_query import encode_query
from app.schemas import (
    AttributeDist,
    Bestseller,
    CategoryEvidence,
    CategoryInsightOutput,
    PriceTier,
    ProviderFailureReason,
    ProviderMetadata,
)

_LOCAL_INSIGHTS: dict[str, dict[str, Any]] = {
    "耳机": {
        "components": ["佩戴方式", "主动降噪", "续航", "重量", "编码协议"],
        "bestsellers": [
            {
                "name": "轻量头戴式降噪耳机",
                "typical_price_cny": 999,
                "why_popular": "降噪、续航和舒适度均衡",
            },
            {"name": "真无线降噪耳机", "typical_price_cny": 699, "why_popular": "便携且适合通勤"},
        ],
        "attributes": [
            {"name": "佩戴方式", "distribution": {"头戴式": 0.46, "入耳式": 0.42, "开放式": 0.12}}
        ],
        "tiers": [
            ("budget", (200, 500), "基础降噪"),
            ("mid", (500, 1200), "主流体验"),
            ("premium", (1200, 3000), "旗舰降噪与材质"),
        ],
    },
    "咖啡机": {
        "components": ["泵压", "温控", "奶泡", "清洁", "体积"],
        "bestsellers": [
            {
                "name": "紧凑型半自动咖啡机",
                "typical_price_cny": 1599,
                "why_popular": "可玩性与占地平衡",
            }
        ],
        "attributes": [
            {"name": "类型", "distribution": {"胶囊": 0.28, "半自动": 0.45, "全自动": 0.27}}
        ],
        "tiers": [
            ("budget", (300, 900), "胶囊或入门机"),
            ("mid", (900, 3000), "稳定萃取"),
            ("premium", (3000, 12000), "完整研磨与奶咖系统"),
        ],
    },
}

COARSE_K = 30
FINE_K_QUICK = 8
FINE_K_DEEP = 15
RERANK_BYPASS_TOP_SCORE = 0.92
SEMANTIC_TOKENS = {"气质", "感觉", "风格", "感", "适合", "送", "氛围"}


def should_disable_bm25(category: str) -> bool:
    """Disable lexical noise for strongly semantic, non-literal category queries."""

    return any(token in category for token in SEMANTIC_TOKENS)


async def _rerank_hits(
    category: str, hits: list[dict[str, Any]], *, top_k: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Rerank only when coarse results need it and a real endpoint is configured."""

    if not hits:
        return [], None
    if len(hits) <= top_k:
        return hits[:top_k], None
    try:
        top_score = float(hits[0].get("_score") or 0)
    except (TypeError, ValueError):
        top_score = 0.0
    if top_score >= RERANK_BYPASS_TOP_SCORE:
        return hits[:top_k], "rerank_bypassed_high_score"
    if not os.getenv("RERANKER_ENDPOINT", "").strip():
        return hits[:top_k], "rerank_not_configured"
    summaries = [str((hit.get("_source") or {}).get("summary") or "") for hit in hits]
    try:
        scores = await HTTPTextReranker().score(category, summaries)
    except Exception as exc:  # noqa: BLE001 - preserve coarse recall on optional failure
        return hits[:top_k], f"reranker unavailable: {type(exc).__name__}"
    ranked = [hit for _, hit in sorted(zip(scores, hits), key=lambda pair: pair[0], reverse=True)]
    return ranked[:top_k], None


def _default_insight(category: str) -> dict[str, Any]:
    return {
        "components": ["核心规格", "材质", "售后", "使用成本"],
        "bestsellers": [
            {
                "name": f"主流{category}",
                "typical_price_cny": None,
                "why_popular": "规格、评价与价格较均衡",
            }
        ],
        "attributes": [
            {"name": "购买关注点", "distribution": {"性能": 0.4, "价格": 0.35, "设计": 0.25}}
        ],
        "tiers": [
            ("budget", (0, 500), "入门选择"),
            ("mid", (500, 1500), "主流选择"),
            ("premium", (1500, 10000), "高阶选择"),
        ],
    }


def curated_category_insight(
    category: str,
    *,
    fallback_reason: str | None = None,
    failure_reason: ProviderFailureReason | None = None,
) -> CategoryInsightOutput:
    data = _LOCAL_INSIGHTS.get(category, _default_insight(category))
    evidence = [
        CategoryEvidence(
            document_id=f"curated-{category}",
            field="curated_card",
            summary="built-in category knowledge card; no external document was available",
            score=1.0,
        )
    ]
    return CategoryInsightOutput(
        category=category,
        components=data["components"],
        bestsellers=[Bestseller.model_validate(item) for item in data["bestsellers"]],
        attributes=[AttributeDist.model_validate(item) for item in data["attributes"]],
        price_tiers=[
            PriceTier(tier=tier, range_cny=price_range, notes=notes)
            for tier, price_range, notes in data["tiers"]
        ],
        confidence=0.86 if category in _LOCAL_INSIGHTS else 0.68,
        evidence=evidence,
        provider=ProviderMetadata(
            source="curated",
            provider="built-in-category-kb",
            status="degraded" if fallback_reason else "ok",
            fallback_reason=fallback_reason,
            failure_reason=failure_reason,
        ),
    )


async def _opensearch_insight_once(category: str, depth: str) -> CategoryInsightOutput:
    base_url = os.environ["OPENSEARCH_URL"].rstrip("/")
    index = os.getenv("OPENSEARCH_CATEGORY_INDEX", "shopping_agent_category_kb")
    auth = None
    if os.getenv("OPENSEARCH_USERNAME"):
        auth = (os.environ["OPENSEARCH_USERNAME"], os.getenv("OPENSEARCH_PASSWORD", ""))
    text_query = {"multi_match": {"query": category, "fields": ["category^2", "summary"]}}
    semantic_unavailable = None
    try:
        vector = await encode_query(category)
        queries: list[dict[str, Any]] = []
        if not should_disable_bm25(category):
            queries.append(text_query)
        queries.append({"knn": {"embedding": {"vector": vector, "k": COARSE_K}}})
        query: dict[str, Any] = {"hybrid": {"queries": queries}}
    except Exception as exc:  # noqa: BLE001 - semantic recall is an optional channel
        query = text_query
        semantic_unavailable = f"semantic channel unavailable: {type(exc).__name__}"
    fine_k = FINE_K_DEEP if depth == "deep" else FINE_K_QUICK
    body = {"size": COARSE_K, "query": query}
    params = {}
    if "hybrid" in query and os.getenv("OPENSEARCH_SEARCH_PIPELINE"):
        params["search_pipeline"] = os.environ["OPENSEARCH_SEARCH_PIPELINE"]
    async with httpx.AsyncClient(
        timeout=get_settings().provider_timeout_seconds, auth=auth
    ) as client:
        response = await client.post(f"{base_url}/{index}/_search", params=params, json=body)
        response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
    if not hits:
        raise LookupError(f"no category cards found for {category}")
    hits, rerank_status = await _rerank_hits(category, hits, top_k=fine_k)
    sources = [
        {
            **(hit.get("_source", {}) if isinstance(hit.get("_source"), dict) else {}),
            "id": hit.get("_id"),
            "_score": hit.get("_score"),
        }
        for hit in hits
    ]
    merged, evidence = extract_structured_card(sources)
    components, bestsellers, attributes, price_tiers, confidence = summarize_card(category, merged)
    return CategoryInsightOutput(
        category=category,
        components=components,
        bestsellers=bestsellers,
        attributes=attributes,
        price_tiers=price_tiers,
        confidence=confidence,
        evidence=evidence,
        provider=ProviderMetadata(
            source="live",
            provider="opensearch",
            status="degraded" if semantic_unavailable or rerank_status else "ok",
            fallback_reason="; ".join(
                reason for reason in (semantic_unavailable, rerank_status) if reason
            )
            or None,
        ),
    )


async def _opensearch_insight(category: str, depth: str) -> CategoryInsightOutput:
    return await get_provider_resilience().execute(
        "opensearch", lambda: _opensearch_insight_once(category, depth)
    )


async def category_insight(
    category: str, depth: Literal["quick", "deep"] = "quick"
) -> CategoryInsightOutput:
    """Return category conclusions from OpenSearch or the local knowledge cards."""

    category = normalize_category(category)
    fallback_reason = None
    if os.getenv("OPENSEARCH_URL"):
        try:
            return await _opensearch_insight(category, depth)
        except ProviderCircuitOpenError as exc:
            fallback_reason = str(exc)
            return curated_category_insight(
                category,
                fallback_reason=fallback_reason,
                failure_reason="circuit_open",
            )
        except Exception as exc:  # noqa: BLE001 - this optional provider degrades independently
            fallback_reason = f"OpenSearch unavailable: {type(exc).__name__}"
    else:
        fallback_reason = "OPENSEARCH_URL is not configured"
    return curated_category_insight(category, fallback_reason=fallback_reason)
