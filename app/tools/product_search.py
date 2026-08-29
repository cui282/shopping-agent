"""OpenSearch product BM25 + ANN search with efficient k-NN filtering."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.data.product_document import candidate_from_product_record
from app.recall.tower_query import encode_query
from app.schemas import Candidate, Platform


class ProductSearchError(RuntimeError):
    """The product index cannot serve a search request."""


@dataclass(frozen=True, slots=True)
class ProductSearchFilters:
    """Scalar constraints applied inside the OpenSearch k-NN query."""

    platform: Platform
    marketplace_id: str | None = None
    price_min_cny: float | None = None
    price_max_cny: float | None = None
    category_ids: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    require_active: bool = True
    require_fresh: bool = False

    def to_query(self) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = [{"term": {"platform": self.platform}}]
        if self.marketplace_id:
            clauses.append({"term": {"marketplace_id": self.marketplace_id}})
        if self.price_min_cny is not None or self.price_max_cny is not None:
            price_range: dict[str, float] = {}
            if self.price_min_cny is not None:
                price_range["gte"] = self.price_min_cny
            if self.price_max_cny is not None:
                price_range["lte"] = self.price_max_cny
            clauses.append({"range": {"price_cny": price_range}})
        if self.category_ids:
            clauses.append({"terms": {"category_ids": list(self.category_ids)}})
        if self.conditions:
            clauses.append({"terms": {"condition": list(self.conditions)}})
        if self.sources:
            clauses.append({"terms": {"source": list(self.sources)}})
        if self.require_active:
            clauses.append({"term": {"is_active": True}})
        if self.require_fresh:
            clauses.append({"range": {"expires_at": {"gte": "now"}}})
        return {"bool": {"filter": clauses}}


@dataclass(frozen=True, slots=True)
class ProductSearchResult:
    candidates: list[Candidate]
    vector_used: bool
    degradation_reason: str | None = None
    total_hits: int = 0


def build_product_search_body(
    query: str,
    *,
    vector: list[float] | None,
    filters: ProductSearchFilters,
    size: int,
    vector_k: int | None = None,
) -> dict[str, Any]:
    """Build a filtered BM25 + ANN request, or BM25 when no vector is available."""

    filter_query = filters.to_query()
    text_query = {
        "bool": {
            "must": {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "title^5",
                        "brand^2",
                        "category_text^3",
                        "description",
                        "searchable_text",
                    ],
                    "type": "best_fields",
                }
            },
            "filter": filter_query,
        }
    }
    if vector is not None:
        # Keep the scalar clauses in the k-NN query so Faiss HNSW applies them during
        # traversal (efficient filtering), not after ANN candidates have been selected.
        knn_query = {
            "knn": {
                "embedding": {
                    "vector": vector,
                    "k": max(size, vector_k or size * 4),
                    "filter": filter_query,
                }
            }
        }
        query_body: dict[str, Any] = {
            "hybrid": {
                "queries": [
                    text_query,
                    knn_query,
                ]
            }
        }
    else:
        query_body = text_query
    return {
        "size": size,
        "_source": {"excludes": ["embedding", "searchable_text"]},
        "query": query_body,
    }


async def search_products(
    query: str,
    platform: Platform,
    *,
    top_k: int,
    settings: Settings,
    filters: ProductSearchFilters | None = None,
) -> ProductSearchResult:
    """Search the configured OpenSearch product index and project hits to Candidates."""

    if not settings.opensearch_url:
        raise ProductSearchError("OPENSEARCH_URL is not configured")
    if not settings.opensearch_product_index:
        raise ProductSearchError("OPENSEARCH_PRODUCT_INDEX is not configured")
    # Snapshot timestamps remain provenance only. The current product ANN path does not
    # reject a row merely because its optional expiry metadata has elapsed.
    effective_filters = filters or ProductSearchFilters(platform=platform)
    vector: list[float] | None = None
    degradation_reason: str | None = None
    if settings.tower_query_endpoint:
        try:
            vector = await encode_query(query)
            if len(vector) != settings.product_search_embedding_dimension:
                raise ValueError(
                    f"query embedding dimension {len(vector)} does not match "
                    f"PRODUCT_SEARCH_EMBEDDING_DIMENSION={settings.product_search_embedding_dimension}"
                )
        except Exception as exc:  # noqa: BLE001 - lexical search remains a valid degradation
            degradation_reason = f"semantic channel unavailable: {type(exc).__name__}"
    else:
        degradation_reason = "TOWER_QUERY_ENDPOINT is not configured"

    body = build_product_search_body(
        query,
        vector=vector,
        filters=effective_filters,
        size=max(1, min(top_k, 50)),
        vector_k=max(top_k * 4, 20),
    )
    auth = None
    username = os.getenv("OPENSEARCH_USERNAME", "").strip()
    if username:
        auth = (username, os.getenv("OPENSEARCH_PASSWORD", ""))
    params: dict[str, str] = {}
    if vector is not None and settings.opensearch_product_search_pipeline:
        params["search_pipeline"] = settings.opensearch_product_search_pipeline
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds, auth=auth) as client:
        try:
            response = await client.post(
                f"{settings.opensearch_url.rstrip('/')}/{settings.opensearch_product_index}/_search",
                params=params,
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProductSearchError(
                f"OpenSearch product search failed: {type(exc).__name__}"
            ) from exc
    hits = payload.get("hits", {}).get("hits", [])
    candidates: list[Candidate] = []
    for hit in hits:
        source = hit.get("_source")
        if not isinstance(source, dict):
            continue
        try:
            candidates.append(candidate_from_product_record(source, hit.get("_score")))
        except (KeyError, TypeError, ValueError):
            continue
    total = payload.get("hits", {}).get("total", 0)
    total_hits = total.get("value", 0) if isinstance(total, dict) else int(total or 0)
    return ProductSearchResult(
        candidates=candidates,
        vector_used=vector is not None,
        degradation_reason=degradation_reason,
        total_hits=total_hits,
    )


__all__ = [
    "ProductSearchError",
    "ProductSearchFilters",
    "ProductSearchResult",
    "build_product_search_body",
    "search_products",
]
