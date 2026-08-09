"""Inference-only cross-encoder adapter for CategoryCard summaries."""

from __future__ import annotations

import math
import os
from collections.abc import Sequence

import httpx


class HTTPTextReranker:
    """Call a provider-owned BGE-style reranker; never fabricate scores locally."""

    async def score(self, query: str, candidates: Sequence[str]) -> list[float]:
        endpoint = os.getenv("RERANKER_ENDPOINT", "").strip()
        if not endpoint:
            raise RuntimeError("RERANKER_ENDPOINT is not configured")
        async with httpx.AsyncClient(
            timeout=float(os.getenv("RERANK_TIMEOUT_SECONDS", "10"))
        ) as client:
            response = await client.post(
                endpoint,
                json={"query": query, "candidates": list(candidates)},
            )
            response.raise_for_status()
            payload = response.json()
        raw = payload.get("scores") if isinstance(payload, dict) else payload
        if not isinstance(raw, list) or len(raw) != len(candidates):
            raise ValueError("reranker response must contain one score per candidate")
        scores = [float(value) for value in raw]
        if not all(math.isfinite(score) for score in scores):
            raise ValueError("reranker response contains non-finite scores")
        return scores


__all__ = ["HTTPTextReranker"]
