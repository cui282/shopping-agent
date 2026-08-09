"""Normalize purchased data-provider feeds into platform-local StandardItems.

This module deliberately stops at the Marketplace Gateway boundary. It does not crawl upstream
marketplaces or claim that an offer from two platforms is the same Product Variant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from app.schemas import Candidate, JsonValue, Platform, ProductIdentity, StrictModel
from app.tools.marketplace_gateway import normalize_gateway_response


class CatalogQuality(StrictModel):
    grade: str = Field(pattern=r"^[ABC]$")
    score: float = Field(ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)


class CatalogMapping(StrictModel):
    """Provider-to-StandardItem mappings kept outside the normalization code."""

    field_mapping: dict[str, str] = Field(default_factory=dict)
    category_mapping: dict[str, list[str]] = Field(default_factory=dict)
    blocked_categories: list[str] = Field(default_factory=list)


class ODSRecord(StrictModel):
    """An immutable raw provider record retained long enough to make DWD reruns possible."""

    record_id: str = Field(min_length=1, max_length=160)
    platform: Platform
    raw: dict[str, JsonValue]
    source_provider: str | None = Field(default=None, max_length=200)
    received_at: str


class ODSBatch(StrictModel):
    """Raw ODS envelope; it is a value object and does not write to runtime storage."""

    platform: Platform
    delivery_mode: str = Field(pattern=r"^(api|batch_file|incremental)$")
    source_provider: str | None = Field(default=None, max_length=200)
    received_at: str
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: list[ODSRecord] = Field(default_factory=list)


class StandardItem(StrictModel):
    """A deduplicated item within one marketplace, retaining every normalized offer."""

    standard_id: str = Field(pattern=r"^[a-z0-9_-]{8,80}$")
    platform: Platform
    title: str = Field(min_length=1, max_length=4000)
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    variant_attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    offers: list[Candidate] = Field(min_length=1)
    quality: CatalogQuality
    category_path: list[str] = Field(default_factory=list)
    price_cny: float | None = Field(default=None, ge=0)
    original_price_cny: float | None = Field(default=None, ge=0)
    currency_raw: str | None = Field(default=None, max_length=16)
    source_updated_at: str | None = None
    ingested_at: str = Field(default_factory=lambda: _utc_now())


class CatalogIngestReport(StrictModel):
    platform: Platform
    input_records: int = Field(ge=0)
    valid_offers: int = Field(ge=0)
    standard_items: int = Field(ge=0)
    rejected_records: int = Field(ge=0)
    grades: dict[str, int] = Field(default_factory=dict)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    ods_batch: ODSBatch | None = None
    items: list[StandardItem] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _category_path(candidate: Candidate, mapping: CatalogMapping | None) -> list[str]:
    raw = candidate.attributes.get("category_path") or candidate.attributes.get("category")
    if isinstance(raw, list):
        path = [str(value).strip() for value in raw if str(value).strip()]
    elif isinstance(raw, str):
        path = [part.strip() for part in raw.replace("/", ">").split(">") if part.strip()]
    else:
        path = []
    if mapping is None or not path:
        return path
    mapped = mapping.category_mapping.get(" > ".join(path))
    return list(mapped) if mapped else path


def _price_cny(candidate: Candidate, fx_rates: dict[str, float] | None) -> float | None:
    if fx_rates is None:
        return candidate.price if candidate.currency.upper() == "CNY" else None
    rate = fx_rates.get(candidate.currency.upper())
    if rate is None or rate <= 0:
        return None
    return round(candidate.price * rate, 4)


def standardize_candidates(
    candidates: Iterable[Candidate],
    platform: Platform,
    *,
    mapping: CatalogMapping | None = None,
    fx_rates: dict[str, float] | None = None,
    ingested_at: str | None = None,
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
                category_path=_category_path(representative, mapping),
                price_cny=_price_cny(representative, fx_rates),
                currency_raw=representative.currency,
                source_updated_at=representative.retrieved_at,
                ingested_at=ingested_at or _utc_now(),
            )
        )
    return items


def _raw_records(payload: Any, *, depth: int = 0) -> list[dict[str, JsonValue]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict) or depth > 4:
        return []
    for key in ("items", "products", "results", "offers", "data"):
        value = payload.get(key)
        if isinstance(value, (list, dict)):
            records = _raw_records(value, depth=depth + 1)
            if records:
                return records
    return [payload] if payload else []


def build_ods_batch(
    payload: Any,
    platform: Platform,
    *,
    source_provider: str | None = None,
    delivery_mode: str = "api",
    received_at: str | None = None,
) -> ODSBatch:
    """Capture provider records before DWD normalization, without persisting credentials."""

    records = _raw_records(payload)
    stamp = received_at or _utc_now()
    canonical = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ods_records = [
        ODSRecord(
            record_id=str(item.get("offer_id") or item.get("item_id") or item.get("id") or index),
            platform=platform,
            raw=item,
            source_provider=source_provider,
            received_at=stamp,
        )
        for index, item in enumerate(records)
    ]
    return ODSBatch(
        platform=platform,
        delivery_mode=delivery_mode,
        source_provider=source_provider,
        received_at=stamp,
        checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        records=ods_records,
    )


def _mapped_payload(payload: Any, mapping: CatalogMapping | None) -> Any:
    if mapping is None:
        return payload
    records = _raw_records(payload)
    mapped: list[dict[str, JsonValue]] = []
    for record in records:
        item = dict(record)
        for source, target in mapping.field_mapping.items():
            if source in item and target not in item:
                item[target] = item[source]
        mapped.append(item)
    return mapped


def _is_stale_unavailable(
    candidate: Candidate, *, now: datetime, unavailable_after_days: int
) -> bool:
    if candidate.availability not in {"out_of_stock", "unavailable"} or not candidate.retrieved_at:
        return False
    try:
        observed = datetime.fromisoformat(candidate.retrieved_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return now - observed.astimezone(timezone.utc) >= timedelta(days=unavailable_after_days)


def _filter_candidates(
    candidates: Iterable[Candidate],
    *,
    mapping: CatalogMapping | None,
    now: datetime,
    unavailable_after_days: int,
) -> tuple[list[Candidate], dict[str, int]]:
    accepted: list[Candidate] = []
    rejected: dict[str, int] = {}
    blocked = {value.casefold() for value in (mapping.blocked_categories if mapping else [])}
    for candidate in candidates:
        category_values = candidate.attributes.get("category_path") or candidate.attributes.get(
            "category"
        )
        category_text = str(category_values).casefold() if category_values is not None else ""
        if candidate.price <= 0:
            reason = "invalid_price"
        elif _is_stale_unavailable(
            candidate, now=now, unavailable_after_days=unavailable_after_days
        ):
            reason = "stale_unavailable"
        elif any(value and value in category_text for value in blocked):
            reason = "blocked_category"
        else:
            accepted.append(candidate)
            continue
        rejected[reason] = rejected.get(reason, 0) + 1
    return accepted, rejected


def ingest_payload(
    payload: Any,
    platform: Platform,
    *,
    mapping: CatalogMapping | None = None,
    fx_rates: dict[str, float] | None = None,
    source_provider: str | None = None,
    delivery_mode: str = "api",
    now: datetime | None = None,
    unavailable_after_days: int = 30,
) -> CatalogIngestReport:
    """Validate one provider payload and return a quality-audited catalog projection."""

    raw_count = _raw_record_count(payload)
    if unavailable_after_days < 0:
        raise ValueError("unavailable_after_days must be non-negative")
    ods_batch = build_ods_batch(
        payload,
        platform,
        source_provider=source_provider,
        delivery_mode=delivery_mode,
    )
    candidates = normalize_gateway_response(_mapped_payload(payload, mapping), platform)
    filtered, rejection_reasons = _filter_candidates(
        candidates,
        mapping=mapping,
        now=now or datetime.now(timezone.utc),
        unavailable_after_days=unavailable_after_days,
    )
    items = standardize_candidates(
        filtered,
        platform,
        mapping=mapping,
        fx_rates=fx_rates,
        ingested_at=ods_batch.received_at,
    )
    grades: dict[str, int] = {}
    for item in items:
        grades[item.quality.grade] = grades.get(item.quality.grade, 0) + 1
    return CatalogIngestReport(
        platform=platform,
        input_records=raw_count,
        valid_offers=len(filtered),
        standard_items=len(items),
        rejected_records=max(0, raw_count - len(filtered)),
        grades=grades,
        rejection_reasons=rejection_reasons,
        ods_batch=ods_batch,
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


def ingest_jsonl(
    path: str | Path,
    platform: Platform,
    *,
    mapping: CatalogMapping | None = None,
    mapping_path: str | Path | None = None,
    fx_rates: dict[str, float] | None = None,
    source_provider: str | None = None,
    unavailable_after_days: int = 30,
) -> CatalogIngestReport:
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
    active_mapping = mapping or (load_mapping(mapping_path) if mapping_path else None)
    return ingest_payload(
        records,
        platform,
        mapping=active_mapping,
        fx_rates=fx_rates,
        source_provider=source_provider,
        delivery_mode="batch_file",
        unavailable_after_days=unavailable_after_days,
    )


def load_mapping(path: str | Path) -> CatalogMapping:
    """Load a provider mapping YAML file; only mapping data is accepted."""

    parsed = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError("catalog mapping must be a YAML object")
    return CatalogMapping.model_validate(parsed)


__all__ = [
    "CatalogIngestReport",
    "CatalogMapping",
    "CatalogQuality",
    "ODSBatch",
    "ODSRecord",
    "StandardItem",
    "build_ods_batch",
    "ingest_jsonl",
    "ingest_payload",
    "load_mapping",
    "standardize_candidates",
]
