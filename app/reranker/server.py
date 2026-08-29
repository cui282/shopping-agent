"""Local BGE-Reranker-v2-m3 HTTP service.

The service accepts the same score contract used by both product and category rerankers. It
loads the pretrained cross-encoder lazily, never trains or persists request content, and exposes
only bounded query/candidate text to the model.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_MAX_LENGTH = 512
DEFAULT_BATCH_SIZE = 16
MAX_CANDIDATES = 100
MAX_QUERY_CHARS = 4000
MAX_PASSAGE_CHARS = 4000


class RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    candidates: list[str | dict[str, Any]] = Field(
        min_length=1,
        max_length=MAX_CANDIDATES,
    )
    model: str | None = Field(default=None, max_length=200)


def _model_name() -> str:
    return os.getenv("RERANKER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _device() -> str:
    return os.getenv("RERANKER_DEVICE", "cpu").strip() or "cpu"


def _max_length() -> int:
    try:
        value = int(os.getenv("RERANKER_MAX_LENGTH", str(DEFAULT_MAX_LENGTH)))
    except ValueError as exc:
        raise RuntimeError("RERANKER_MAX_LENGTH must be an integer") from exc
    if value < 32 or value > 8192:
        raise RuntimeError("RERANKER_MAX_LENGTH must be between 32 and 8192")
    return value


def _batch_size() -> int:
    try:
        value = int(os.getenv("RERANKER_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
    except ValueError as exc:
        raise RuntimeError("RERANKER_BATCH_SIZE must be an integer") from exc
    if value < 1 or value > MAX_CANDIDATES:
        raise RuntimeError(f"RERANKER_BATCH_SIZE must be between 1 and {MAX_CANDIDATES}")
    return value


@lru_cache(maxsize=1)
def _model() -> Any:
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError("install the index extra to run the BGE reranker service") from exc
    return CrossEncoder(
        _model_name(),
        max_length=_max_length(),
        device=_device(),
    )


def _candidate_text(value: str | dict[str, Any]) -> str:
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, Mapping):
        parts: list[str] = []
        for key in ("title", "summary", "text", "content", "description", "category"):
            item = value.get(key)
            if item is not None and str(item).strip():
                parts.append(str(item).strip())
        attributes = value.get("attributes")
        if isinstance(attributes, Mapping):
            parts.extend(
                f"{key}: {item}"
                for key, item in attributes.items()
                if item is not None and str(item).strip()
            )
        text = " | ".join(parts)
    else:
        text = ""
    if not text:
        raise HTTPException(status_code=422, detail="candidate must contain text")
    return text[:MAX_PASSAGE_CHARS]


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-min(value, 700.0)))
    exponential = math.exp(max(value, -700.0))
    return exponential / (1.0 + exponential)


def _predict(query: str, passages: list[str]) -> list[float]:
    pairs = [(query, passage) for passage in passages]
    values = _model().predict(
        pairs,
        batch_size=_batch_size(),
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    if hasattr(values, "reshape"):
        values = values.reshape(-1).tolist()
    if not isinstance(values, (list, tuple)) or len(values) != len(passages):
        raise RuntimeError("BGE reranker returned an invalid score list")
    scores = [float(value) for value in values]
    if not all(math.isfinite(value) for value in scores):
        raise RuntimeError("BGE reranker returned a non-finite score")
    return [_sigmoid(value) for value in scores]


app = FastAPI(title="Shopping Agent BGE Reranker", version="1.0")


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        await asyncio.to_thread(_model)
    except Exception as exc:  # noqa: BLE001 - health reports unavailable without crashing
        return {
            "status": "unavailable",
            "model": _model_name(),
            "device": _device(),
            "reason": type(exc).__name__,
        }
    return {
        "status": "ok",
        "model": _model_name(),
        "device": _device(),
        "max_length": _max_length(),
        "batch_size": _batch_size(),
    }


@app.post("/score")
async def score(payload: RerankRequest) -> dict[str, Any]:
    configured_model = _model_name()
    if payload.model and payload.model != configured_model:
        raise HTTPException(
            status_code=400,
            detail=f"reranker serves {configured_model}, not {payload.model}",
        )
    passages = [_candidate_text(candidate) for candidate in payload.candidates]
    try:
        scores = await asyncio.to_thread(_predict, payload.query.strip(), passages)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"BGE reranker unavailable: {type(exc).__name__}",
        ) from exc
    return {
        "model": configured_model,
        "scores": scores,
    }


__all__ = ["app"]
