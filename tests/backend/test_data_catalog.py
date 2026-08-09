from __future__ import annotations

import json

from app.data.catalog import ingest_jsonl, ingest_payload, standardize_candidates
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
