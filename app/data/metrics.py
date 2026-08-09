"""Deterministic DWD/ADS metrics for purchased catalog data and trace feedback."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from app.data.catalog import CatalogIngestReport
from app.schemas import Platform, StrictModel


class CatalogMetrics(StrictModel):
    """An ADS snapshot derived from a DWD ingest report, never from fixture data."""

    platform: Platform
    generated_at: str
    input_records: int = Field(ge=0)
    valid_offers: int = Field(ge=0)
    standard_items: int = Field(ge=0)
    rejected_records: int = Field(ge=0)
    quality_grade_counts: dict[str, int] = Field(default_factory=dict)
    availability_rate: float = Field(ge=0, le=1)
    price_tiers_cny: dict[str, float | None] = Field(default_factory=dict)
    top_categories: list[tuple[str, int]] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    provider_success_rate: float | None = Field(default=None, ge=0, le=1)
    selected_count: int = Field(default=0, ge=0)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return round(value, 4)


def _trace_feedback(
    events: Iterable[dict[str, Any]], platform: Platform
) -> tuple[float | None, int, list[str]]:
    attempts = successes = selected = 0
    gaps: set[str] = set()
    for event in events:
        if str(event.get("platform", "")).casefold() != platform:
            continue
        if event.get("selected") is True:
            selected += 1
        if "success" in event:
            attempts += 1
            successes += int(bool(event["success"]))
        elif event.get("status") in {"ok", "success", "degraded", "error", "unavailable"}:
            attempts += 1
            successes += int(event.get("status") in {"ok", "success", "degraded"})
        result_count = event.get("result_count", event.get("total_recall"))
        if result_count == 0:
            query = str(event.get("query", "")).strip()
            if query:
                gaps.add(query)
    rate = round(successes / attempts, 4) if attempts else None
    return rate, selected, sorted(gaps)


def build_catalog_metrics(
    report: CatalogIngestReport,
    *,
    fx_rates: dict[str, float] | None = None,
    trace_events: Iterable[dict[str, Any]] = (),
    top_n: int = 10,
    generated_at: str | None = None,
) -> CatalogMetrics:
    """Build quality, price-tier, and feedback metrics for one platform batch.

    Prices are included only when a trusted CNY conversion rate is supplied (or the offer is
    already in CNY). This keeps an unknown FX rate visible instead of silently estimating it.
    """

    if top_n < 1:
        raise ValueError("top_n must be positive")
    rates = {str(key).upper(): float(value) for key, value in (fx_rates or {}).items()}
    prices: list[float] = []
    categories: Counter[str] = Counter()
    available = 0
    offer_count = 0
    for item in report.items:
        categories.update({" > ".join(item.category_path): 1} if item.category_path else {})
        for offer in item.offers:
            offer_count += 1
            if offer.availability not in {"out_of_stock", "unavailable"}:
                available += 1
            rate = rates.get(offer.currency.upper())
            if rate is None and offer.currency.upper() == "CNY":
                rate = 1.0
            if rate is not None and rate > 0:
                prices.append(offer.price * rate)
    success_rate, selected_count, coverage_gaps = _trace_feedback(trace_events, report.platform)
    return CatalogMetrics(
        platform=report.platform,
        generated_at=generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        input_records=report.input_records,
        valid_offers=report.valid_offers,
        standard_items=report.standard_items,
        rejected_records=report.rejected_records,
        quality_grade_counts=dict(report.grades),
        availability_rate=round(available / offer_count, 4) if offer_count else 0,
        price_tiers_cny={
            "p25": _percentile(prices, 0.25),
            "p50": _percentile(prices, 0.50),
            "p75": _percentile(prices, 0.75),
        },
        top_categories=categories.most_common(top_n),
        coverage_gaps=coverage_gaps,
        provider_success_rate=success_rate,
        selected_count=selected_count,
    )


__all__ = ["CatalogMetrics", "build_catalog_metrics"]
