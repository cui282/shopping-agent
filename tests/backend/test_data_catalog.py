from __future__ import annotations

import json
from datetime import datetime, timezone

from app.data.catalog import (
    CatalogMapping,
    ingest_jsonl,
    ingest_payload,
    load_mapping,
    standardize_candidates,
)
from app.data.metrics import build_catalog_metrics
from app.schemas import Candidate, OfferProvenance, ProductIdentity


def _candidate(item_id: str, platform: str, title: str, *, mpn: str | None = None) -> Candidate:
    return Candidate(
        item_id=item_id,
        platform=platform,  # type: ignore[arg-type]
        title=title,
        price=100,
        currency="USD",
        source="live",
        offer_id=item_id,
        product_url=f"https://example.com/{item_id}",
        identity=ProductIdentity(brand="Acme", model=mpn),
        provenance=OfferProvenance(kind="marketplace_gateway", provider="licensed-feed"),
    )


def test_standardize_candidates_merges_offers_only_inside_one_platform() -> None:
    amazon = _candidate("amazon-1", "amazon", "Acme X1", mpn="X1")
    amazon_offer = _candidate("amazon-2", "amazon", "Acme X1", mpn="X1")
    ebay = _candidate("ebay-1", "ebay", "Acme X1", mpn="X1")

    items = standardize_candidates([amazon, amazon_offer, ebay], "amazon")

    assert len(items) == 1
    assert items[0].platform == "amazon"
    assert {offer.offer_id for offer in items[0].offers} == {"amazon-1", "amazon-2"}
    assert standardize_candidates([amazon, amazon_offer, ebay], "ebay")[0].offers == [ebay]


def test_standardize_candidates_uses_the_highest_quality_offer_as_representative() -> None:
    incomplete = _candidate("amazon-incomplete", "amazon", "Acme X1", mpn="X1").model_copy(
        update={"offer_id": None, "product_url": None}
    )
    complete = _candidate("amazon-complete", "amazon", "Acme X1", mpn="X1")

    item = standardize_candidates([incomplete, complete], "amazon")[0]

    assert item.identity.model == "X1"
    assert item.quality.grade == "A"


def test_ingest_payload_reports_invalid_records_and_quality() -> None:
    report = ingest_payload(
        {
            "items": [
                {
                    "offer_id": "valid-1",
                    "title": "Acme X1",
                    "price": 100,
                    "currency": "usd",
                    "product_url": "https://example.com/valid-1",
                    "identity": {"brand": "Acme", "model": "X1"},
                },
                {"offer_id": "invalid", "price": 1},
            ]
        },
        "amazon",
    )

    assert report.input_records == 2
    assert report.valid_offers == 1
    assert report.rejected_records == 1
    assert report.standard_items == 1
    assert report.items[0].quality.grade == "A"


def test_ingest_jsonl_accepts_provider_records(tmp_path) -> None:
    path = tmp_path / "offers.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "offer_id": "one",
                        "title": "First",
                        "price": 10,
                        "currency": "USD",
                    }
                ),
                json.dumps(
                    {
                        "offer_id": "two",
                        "title": "Second",
                        "price": 20,
                        "currency": "USD",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    report = ingest_jsonl(path, "amazon")

    assert report.input_records == 2
    assert report.valid_offers == 2
    assert report.standard_items == 2


def test_ingest_keeps_ods_and_filters_stale_or_invalid_records() -> None:
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    report = ingest_payload(
        {
            "items": [
                {
                    "offer_id": "good",
                    "title": "Good",
                    "price": 10,
                    "currency": "USD",
                    "retrieved_at": "2026-08-09T00:00:00Z",
                },
                {
                    "offer_id": "old",
                    "title": "Old",
                    "price": 10,
                    "currency": "USD",
                    "availability": "out_of_stock",
                    "retrieved_at": "2026-06-01T00:00:00Z",
                },
                {
                    "offer_id": "bad-price",
                    "title": "Bad",
                    "price": 0,
                    "currency": "USD",
                },
            ]
        },
        "amazon",
        source_provider="licensed-feed",
        now=now,
    )

    assert report.valid_offers == 1
    assert report.rejection_reasons == {"stale_unavailable": 1, "invalid_price": 1}
    assert report.ods_batch is not None
    assert len(report.ods_batch.records) == 3
    assert len(report.ods_batch.checksum) == 64


def test_mapping_and_ads_metrics_are_deterministic(tmp_path) -> None:
    mapping_path = tmp_path / "amazon.yml"
    mapping_path.write_text(
        "field_mapping:\n  item_name: title\ncategory_mapping: {}\nblocked_categories: []\n",
        encoding="utf-8",
    )
    mapping = load_mapping(mapping_path)
    assert mapping == CatalogMapping(field_mapping={"item_name": "title"})
    report = ingest_payload(
        {
            "items": [
                {
                    "offer_id": "one",
                    "item_name": "One",
                    "price": 100,
                    "currency": "USD",
                    "category": "Home > Kitchen",
                    "availability": "in_stock",
                },
                {
                    "offer_id": "two",
                    "item_name": "Two",
                    "price": 200,
                    "currency": "USD",
                    "category": "Home > Kitchen",
                    "availability": "out_of_stock",
                },
            ]
        },
        "amazon",
        mapping=mapping,
    )
    metrics = build_catalog_metrics(
        report,
        fx_rates={"USD": 7},
        trace_events=[
            {"platform": "amazon", "status": "success", "query": "kitchen", "result_count": 2},
            {"platform": "amazon", "status": "error", "query": "rare", "result_count": 0},
            {"platform": "amazon", "selected": True},
        ],
        generated_at="2026-08-10T00:00:00Z",
    )

    assert metrics.price_tiers_cny == {"p25": 875.0, "p50": 1050.0, "p75": 1225.0}
    assert metrics.availability_rate == 0.5
    assert metrics.provider_success_rate == 0.5
    assert metrics.coverage_gaps == ["rare"]
    assert metrics.selected_count == 1
