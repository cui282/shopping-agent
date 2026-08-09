"""Small, typed RAG pipeline for category knowledge cards.

The pipeline is deliberately evidence-first: retrieval returns documents, extraction accepts only
structured fields supplied by those documents, and summarization formats a validated card. It does
not ask an LLM to invent a bestseller, price tier, or attribute distribution.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.schemas import (
    AttributeDist,
    Bestseller,
    CategoryEvidence,
    PriceTier,
)


def extract_structured_card(
    sources: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], list[CategoryEvidence]]:
    """Extract the first valid structured card and retain provenance for each selected field."""

    evidence: list[CategoryEvidence] = []
    for index, source in enumerate(sources):
        structured = source.get("structured")
        if not isinstance(structured, dict):
            continue
        document_id = str(source.get("id") or source.get("document_id") or f"card-{index + 1}")
        valid = {
            "components": structured.get("components", []),
            "bestsellers": structured.get("bestsellers", []),
            "attributes": structured.get("attributes", []),
            "price_tiers": structured.get("price_tiers", []),
            "confidence": structured.get("confidence", 0.7),
        }
        if not any(
            valid[field] not in (None, [], "")
            for field in ("components", "bestsellers", "attributes", "price_tiers")
        ):
            continue
        for field, value in valid.items():
            if value in (None, [], ""):
                continue
            try:
                score = max(0.0, float(source.get("_score") or 0))
            except (TypeError, ValueError):
                score = 0.0
            evidence.append(
                CategoryEvidence(
                    document_id=document_id,
                    field=field,
                    summary=f"structured.{field} extracted from category knowledge document",
                    score=score,
                )
            )
        return valid, evidence
    raise LookupError("retrieval returned no structured category card")


def summarize_card(
    category: str, structured: dict[str, Any]
) -> tuple[list[str], list[Bestseller], list[AttributeDist], list[PriceTier], float]:
    """Validate extracted fields into the stable CategoryInsight contract."""

    components = [str(value) for value in structured.get("components", []) if str(value).strip()]
    bestsellers = [Bestseller.model_validate(item) for item in structured.get("bestsellers", [])]
    attributes = [AttributeDist.model_validate(item) for item in structured.get("attributes", [])]
    tiers = [PriceTier.model_validate(item) for item in structured.get("price_tiers", [])]
    confidence = float(structured.get("confidence", 0.7))
    if not 0 <= confidence <= 1:
        raise ValueError(f"invalid RAG confidence for {category}")
    return components, bestsellers, attributes, tiers, confidence


__all__ = ["extract_structured_card", "summarize_card"]
