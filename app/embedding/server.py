"""Small, inference-only SentenceTransformer HTTP service.

The service exposes the repository's existing Query Tower contract and an OpenAI-compatible
``/v1/embeddings`` endpoint. It never persists request text or generated vectors.
"""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    input: str | list[str] | None = None
    query: str | None = None
    text: str | None = None


class EmbeddingResponse(BaseModel):
    embedding: list[float]


class OpenAIEmbeddingData(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float]


class OpenAIEmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[OpenAIEmbeddingData]
    model: str
    usage: dict[str, int] = Field(default_factory=lambda: {"prompt_tokens": 0, "total_tokens": 0})


def _model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _model_path() -> str | None:
    configured = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
    return configured or None


@lru_cache(maxsize=1)
def _model() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("install the index extra to run the embedding service") from exc
    path = _model_path() or _model_name()
    model = SentenceTransformer(path, device=os.getenv("EMBEDDING_DEVICE", "cpu"))
    expected = os.getenv("EMBEDDING_DIMENSION", "").strip()
    if expected:
        dimension = model.get_sentence_embedding_dimension()
        if int(expected) != int(dimension):
            raise RuntimeError(
                f"embedding model dimension {dimension} does not match EMBEDDING_DIMENSION={expected}"
            )
    return model


def _texts(payload: EmbeddingRequest) -> list[str]:
    if payload.input is not None:
        values = [payload.input] if isinstance(payload.input, str) else payload.input
    elif payload.query is not None:
        values = [payload.query]
    elif payload.text is not None:
        values = [payload.text]
    else:
        raise HTTPException(status_code=422, detail="input, query, or text is required")
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise HTTPException(status_code=422, detail="embedding input must contain non-empty text")
    max_texts = int(os.getenv("EMBEDDING_MAX_BATCH", "64"))
    if len(values) > max_texts:
        raise HTTPException(
            status_code=413, detail=f"embedding batch cannot exceed {max_texts} texts"
        )
    return [value.strip() for value in values]


async def _encode(texts: list[str]) -> list[list[float]]:
    try:
        vectors = await asyncio.to_thread(
            _model().encode,
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"embedding model unavailable: {type(exc).__name__}"
        ) from exc
    return [[float(value) for value in vector] for vector in vectors]


app = FastAPI(title="Shopping Agent Embedding Service", version="1.0")


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        dimension = await asyncio.to_thread(_model().get_sentence_embedding_dimension)
    except Exception as exc:  # noqa: BLE001 - health must report unavailable, not crash the process
        return {"status": "unavailable", "model": _model_name(), "reason": type(exc).__name__}
    return {"status": "ok", "model": _model_name(), "dimension": int(dimension)}


@app.post("/embed", response_model=EmbeddingResponse)
async def embed(payload: EmbeddingRequest) -> EmbeddingResponse:
    texts = _texts(payload)
    if len(texts) != 1:
        raise HTTPException(status_code=422, detail="/embed accepts exactly one text")
    vectors = await _encode(texts)
    return EmbeddingResponse(embedding=vectors[0])


@app.post("/v1/embeddings", response_model=OpenAIEmbeddingResponse)
async def openai_embeddings(payload: EmbeddingRequest) -> OpenAIEmbeddingResponse:
    texts = _texts(payload)
    vectors = await _encode(texts)
    return OpenAIEmbeddingResponse(
        data=[
            OpenAIEmbeddingData(index=index, embedding=vector)
            for index, vector in enumerate(vectors)
        ],
        model=_model_name(),
    )


__all__ = ["app"]
