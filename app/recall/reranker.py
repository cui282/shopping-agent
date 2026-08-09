"""Inference-only reranker adapter.

The service may point this at a pre-trained cross-encoder endpoint. No fine-tuning, user tower, or
model checkpoint is bundled with Shopping Agent; unavailable reranking simply preserves the dual
tower and deterministic order.
"""

from __future__ import annotations

import math
import os
from typing import Any, Protocol

import httpx

from app.schemas import Candidate


class Reranker(Protocol):
    async def score(self, query: str, candidates: list[Candidate]) -> list[float]: ...


class HTTPReranker:
    async def score(self, query: str, candidates: list[Candidate]) -> list[float]:
        endpoint = os.getenv("RERANKER_ENDPOINT", "").strip()
        if not endpoint:
            raise RuntimeError("RERANKER_ENDPOINT is not configured")
        payload: dict[str, Any] = {
            "query": query,
            "candidates": [
                {
                    "item_id": candidate.item_id,
                    "title": candidate.title,
                    "attributes": candidate.attributes,
                }
                for candidate in candidates
            ],
        }
        async with httpx.AsyncClient(
            timeout=float(os.getenv("RERANK_TIMEOUT_SECONDS", "10"))
        ) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()
        raw = body.get("scores") if isinstance(body, dict) else body
        if not isinstance(raw, list) or len(raw) != len(candidates):
            raise ValueError("reranker response must contain one score per candidate")
        scores = [float(value) for value in raw]
        if not all(math.isfinite(value) for value in scores):
            raise ValueError("reranker response contains non-finite scores")
        return scores


__all__ = ["HTTPReranker", "Reranker"]
