from __future__ import annotations

import math
import os
import re
from typing import Any, Literal

import httpx

from app.config import get_settings
from app.provider_resilience import ProviderCircuitOpenError, get_provider_resilience
from app.recall.category_norm import category_search_context, normalize_category
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
SEMANTIC_TOKENS = {"气质", "感觉", "风格", "感", "适合", "送", "氛围"}
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


def should_disable_bm25(category: str) -> bool:
    """Disable lexical noise for strongly semantic, non-literal category queries."""

    return any(token in category for token in SEMANTIC_TOKENS)


def _semantic_score(query_vector: list[float] | None, source: dict[str, Any]) -> float | None:
    if not query_vector:
        return None
    raw = source.get("embedding")
    if not isinstance(raw, list) or len(raw) != len(query_vector):
        return None
    try:
        values = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    query_norm = math.sqrt(sum(value * value for value in query_vector))
    value_norm = math.sqrt(sum(value * value for value in values))
    if query_norm <= 0 or value_norm <= 0:
        return None
    cosine = sum(left * right for left, right in zip(query_vector, values)) / (
        query_norm * value_norm
    )
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


def _lexical_score(category: str, source: dict[str, Any]) -> float:
    query = category.casefold().strip()
    if not query:
        return 0.0
    text = " ".join(str(source.get(key) or "") for key in ("category", "summary")).casefold()
    if not text:
        return 0.0
    tokens = set(_TOKEN_PATTERN.findall(query))
    overlap = (
        len(tokens.intersection(_TOKEN_PATTERN.findall(text))) / len(tokens) if tokens else 0.0
    )
    exact = 1.0 if query in text else 0.0
    return min(1.0, overlap * 0.65 + exact * 0.35)


def _local_rerank_hits(
    category: str, hits: list[dict[str, Any]], *, top_k: int, query_vector: list[float] | None
) -> list[dict[str, Any]]:
    """Rank CategoryCards with the shared embedding plus transparent lexical signals."""

    scored: list[tuple[float, float, float, int, dict[str, Any]]] = []
    for position, hit in enumerate(hits):
        source = hit.get("_source")
        source = source if isinstance(source, dict) else {}
        semantic = _semantic_score(query_vector, source)
        lexical = _lexical_score(category, source)
        try:
            confidence = max(0.0, min(1.0, float(source.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        score = (
            semantic * 0.70 + lexical * 0.25 + confidence * 0.05
            if semantic is not None
            else lexical * 0.75 + confidence * 0.25
        )
        try:
            coarse_score = float(hit.get("_score") or 0.0)
        except (TypeError, ValueError):
            coarse_score = 0.0
        scored.append((score, semantic or 0.0, coarse_score, -position, hit))
    scored.sort(key=lambda item: item[:4], reverse=True)
    return [item[-1] for item in scored[:top_k]]


async def _rerank_hits(
    category: str,
    hits: list[dict[str, Any]],
    *,
    top_k: int,
    query_vector: list[float] | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Rerank coarse results with an optional provider or the shared local embedding ranker."""

    if not hits:
        return [], None
    if len(hits) <= top_k:
        return hits[:top_k], None
    if os.getenv("RERANKER_ENDPOINT", "").strip():
        summaries = [str((hit.get("_source") or {}).get("summary") or "") for hit in hits]
        try:
            scores = await HTTPTextReranker().score(category, summaries)
        except Exception:  # noqa: BLE001 - local ranker remains a usable fallback
            scores = None
        if scores is not None:
            ranked = [
                hit for _, hit in sorted(zip(scores, hits), key=lambda pair: pair[0], reverse=True)
            ]
            return ranked[:top_k], None
    return _local_rerank_hits(
        category,
        hits,
        top_k=top_k,
        query_vector=query_vector,
    ), None


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
    context = category_search_context(category)
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
        standard_category=context.standard_category,
        query_expansions=list(context.query_expansions),
        subtypes=list(context.subtypes),
        category_ids=list(context.category_ids),
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
    context = category_search_context(category)
    knowledge_query = " ".join(context.query_terms) or category
    text_query = {"multi_match": {"query": knowledge_query, "fields": ["category^2", "summary"]}}
    semantic_unavailable = None
    query_vector: list[float] | None = None
    try:
        query_vector = await encode_query(knowledge_query)
        queries: list[dict[str, Any]] = []
        if not should_disable_bm25(category):
            queries.append(text_query)
        queries.append({"knn": {"embedding": {"vector": query_vector, "k": COARSE_K}}})
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
    hits, rerank_status = await _rerank_hits(
        category,
        hits,
        top_k=fine_k,
        query_vector=query_vector,
    )
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
        standard_category=context.standard_category,
        query_expansions=list(context.query_expansions),
        subtypes=list(context.subtypes),
        category_ids=list(context.category_ids),
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
