"""Normalize purchased data-provider feeds into platform-local StandardItems.

This module deliberately stops at the Marketplace Gateway boundary. It does not crawl upstream
marketplaces or claim that an offer from two platforms is the same Product Variant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import Field

from app.schemas import Candidate, Platform, ProductIdentity, StrictModel
from app.tools.marketplace_gateway import normalize_gateway_response


class CatalogQuality(StrictModel):
    grade: str = Field(pattern=r"^[ABC]$")
    score: float = Field(ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)


class StandardItem(StrictModel):
    """A deduplicated item within one marketplace, retaining every normalized offer."""

    standard_id: str = Field(pattern=r"^[a-z0-9_-]{8,80}$")
    platform: Platform
    title: str = Field(min_length=1, max_length=4000)
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    variant_attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    offers: list[Candidate] = Field(min_length=1)
    quality: CatalogQuality


class CatalogIngestReport(StrictModel):
    platform: Platform
    input_records: int = Field(ge=0)
    valid_offers: int = Field(ge=0)
    standard_items: int = Field(ge=0)
    rejected_records: int = Field(ge=0)
    grades: dict[str, int] = Field(default_factory=dict)
    items: list[StandardItem] = Field(default_factory=list)


def _identity_key(candidate: Candidate) -> str:
    identity = candidate.identity
    parts = [identity.gtin, identity.mpn, identity.brand, identity.model]
    variant = json.dumps(candidate.variant_attributes, ensure_ascii=False, sort_keys=True)
    if any(parts):
        return "|".join(item or "" for item in parts) + "|" + variant
    title = " ".join(candidate.title.casefold().split())
    return f"{title}|{variant}"


def _quality(candidate: Candidate) -> CatalogQuality:
    missing: list[str] = []
    if not candidate.offer_id:
        missing.append("offer_id")
    if not candidate.product_url:
        missing.append("product_url")
    if not any((candidate.identity.gtin, candidate.identity.mpn, candidate.identity.model)):
        missing.append("identity")
    score = (4 - len(missing)) / 4
    grade = "A" if score >= 1 else "B" if score >= 0.5 else "C"
    return CatalogQuality(grade=grade, score=score, missing_fields=missing)


def standardize_candidates(
    candidates: Iterable[Candidate], platform: Platform
) -> list[StandardItem]:
    """Group offers only within the same platform; never merge cross-platform offers."""

    grouped: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.platform != platform:
            continue
        grouped.setdefault(_identity_key(candidate), []).append(candidate)

    items: list[StandardItem] = []
    for key, offers in grouped.items():
        raw_id = f"{platform}|{key}".encode()
        standard_id = hashlib.sha256(raw_id).hexdigest()[:20]
        quality_values = [_quality(offer) for offer in offers]
        best_index = max(range(len(offers)), key=lambda index: quality_values[index].score)
        representative = offers[best_index]
        best = quality_values[best_index]
        items.append(
            StandardItem(
                standard_id=standard_id,
                platform=platform,
                title=representative.title,
                identity=representative.identity,
                variant_attributes=representative.variant_attributes,
                offers=offers,
                quality=best,
            )
        )
    return items


def ingest_payload(payload: Any, platform: Platform) -> CatalogIngestReport:
    """Validate one provider payload and return a quality-audited catalog projection."""

    raw_count = _raw_record_count(payload)
    candidates = normalize_gateway_response(payload, platform)
    items = standardize_candidates(candidates, platform)
    grades: dict[str, int] = {}
    for item in items:
        grades[item.quality.grade] = grades.get(item.quality.grade, 0) + 1
    return CatalogIngestReport(
        platform=platform,
        input_records=raw_count,
        valid_offers=len(candidates),
        standard_items=len(items),
        rejected_records=max(0, raw_count - len(candidates)),
        grades=grades,
        items=items,
    )


def _raw_record_count(payload: Any, *, depth: int = 0) -> int:
    """Count provider records through the supported gateway wrappers for rejection metrics."""

    if isinstance(payload, list):
        return sum(1 for item in payload if isinstance(item, dict))
    if not isinstance(payload, dict) or depth > 4:
        return 0
    for key in ("items", "products", "results", "offers"):
        if key in payload:
            return _raw_record_count(payload[key], depth=depth + 1)
    if "data" in payload:
        return _raw_record_count(payload["data"], depth=depth + 1)
    return 1 if payload else 0


def ingest_jsonl(path: str | Path, platform: Platform) -> CatalogIngestReport:
    """Ingest newline-delimited provider payloads without adding a runtime database dependency."""

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        if isinstance(value, dict):
            records.append(value)
    return ingest_payload(records, platform)


__all__ = [
    "CatalogIngestReport",
    "CatalogQuality",
    "StandardItem",
    "ingest_jsonl",
    "ingest_payload",
    "standardize_candidates",
]
