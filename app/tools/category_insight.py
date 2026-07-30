from __future__ import annotations

import os
from typing import Any, Literal

import httpx

from app.config import get_settings
from app.recall.tower_query import encode_query
from app.schemas import (
    AttributeDist,
    Bestseller,
    CategoryInsightOutput,
    PriceTier,
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


async def _opensearch_insight(category: str, depth: str) -> CategoryInsightOutput:
    base_url = os.environ["OPENSEARCH_URL"].rstrip("/")
    index = os.getenv("OPENSEARCH_CATEGORY_INDEX", "shopping_agent_category_kb")
    auth = None
    if os.getenv("OPENSEARCH_USERNAME"):
        auth = (os.environ["OPENSEARCH_USERNAME"], os.getenv("OPENSEARCH_PASSWORD", ""))
    text_query = {"multi_match": {"query": category, "fields": ["category^2", "summary"]}}
    semantic_unavailable = None
    try:
        vector = await encode_query(category)
        query: dict[str, Any] = {
            "hybrid": {
                "queries": [
                    text_query,
                    {"knn": {"embedding": {"vector": vector, "k": 8}}},
                ]
            }
        }
    except Exception as exc:  # noqa: BLE001 - semantic recall is an optional channel
        query = text_query
        semantic_unavailable = f"semantic channel unavailable: {type(exc).__name__}"
    body = {"size": 8 if depth == "deep" else 4, "query": query}
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
    sources = [hit.get("_source", {}) for hit in hits]
    merged = next((source for source in sources if source.get("structured")), {}).get("structured")
    if not isinstance(merged, dict):
        raise TypeError("OpenSearch documents do not contain structured insight fields")
    return CategoryInsightOutput(
        category=category,
        components=merged.get("components", []),
        bestsellers=[Bestseller.model_validate(item) for item in merged.get("bestsellers", [])],
        attributes=[AttributeDist.model_validate(item) for item in merged.get("attributes", [])],
        price_tiers=[PriceTier.model_validate(item) for item in merged.get("price_tiers", [])],
        confidence=float(merged.get("confidence", 0.7)),
        provider=ProviderMetadata(
            source="live", provider="opensearch", fallback_reason=semantic_unavailable
        ),
    )


async def category_insight(
    category: str, depth: Literal["quick", "deep"] = "quick"
) -> CategoryInsightOutput:
    """Return category conclusions from OpenSearch or the local knowledge cards."""

    fallback_reason = None
    if os.getenv("OPENSEARCH_URL"):
        try:
            return await _opensearch_insight(category, depth)
        except Exception as exc:  # noqa: BLE001 - this optional provider degrades independently
            fallback_reason = f"OpenSearch unavailable: {type(exc).__name__}"
    else:
        fallback_reason = "OPENSEARCH_URL is not configured"
    data = _LOCAL_INSIGHTS.get(category, _default_insight(category))
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
        provider=ProviderMetadata(
            source="curated",
            provider="built-in-category-kb",
            status="degraded" if fallback_reason else "ok",
            fallback_reason=fallback_reason,
        ),
    )
