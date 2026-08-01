from __future__ import annotations

import pytest

from app.schemas import (
    ItemPickerOutput,
    LandedCost,
    OfferProvenance,
    ProductIdentity,
    ShoppingPlan,
)
from app.tools.decision_engine import decision_engine
from app.tools.identity_matcher import classify_exact_offers, match_offer_identity
from app.tools.shopping_summary import shopping_summary
from app.utils.thread_ctx import thread_scope


def offer(
    item_id: str,
    *,
    title: str = "Acme X1 headphones",
    offer_id: str | None = None,
    identity: ProductIdentity | None = None,
    variant_attributes: dict[str, str | int | float | bool | None] | None = None,
    landed_cny: float = 800,
) -> LandedCost:
    return LandedCost(
        item_id=item_id,
        platform="amazon" if item_id.startswith("amazon") else "ebay",
        offer_id=offer_id,
        title=title,
        price=100,
        currency="USD",
        price_cny=718,
        shipping_cny=40,
        duty_cny=42,
        landed_cny=landed_cny,
        eta_days=10,
        duty_tier="标准",
        identity=identity or ProductIdentity(),
        variant_attributes=variant_attributes or {},
        source="live",
        provenance=OfferProvenance(
            kind="marketplace_gateway",
            provider="test-gateway",
            upstream_source="test-catalog",
        ),
    )


@pytest.mark.parametrize(
    ("reference", "candidate", "decision", "basis"),
    [
        (
            offer("amazon-identifier", identity=ProductIdentity(gtin="4006381333931")),
            offer("ebay-identifier", identity=ProductIdentity(gtin="4006381333931")),
            "matching_offer",
            "identifier",
        ),
        (
            offer(
                "amazon-attributes",
                identity=ProductIdentity(brand="Acme", model="X1"),
                variant_attributes={
                    "capacity": "256 GB",
                    "regional_version": "Global",
                    "bundle": "headphones only",
                    "condition": "new",
                },
            ),
            offer(
                "ebay-attributes",
                identity=ProductIdentity(brand="acme", model="x1"),
                variant_attributes={
                    "capacity": "256GB",
                    "regional_version": "global",
                    "bundle": "headphones only",
                    "condition": "new",
                },
            ),
            "matching_offer",
            "material_variant_attributes",
        ),
        (
            offer("amazon-title-only"),
            offer("ebay-title-only", title="Acme X1 headphones same title"),
            "alternative_candidate",
            "insufficient",
        ),
        (
            offer(
                "amazon-region",
                identity=ProductIdentity(gtin="4006381333931"),
                variant_attributes={"regional_version": "CN"},
            ),
            offer(
                "ebay-region",
                identity=ProductIdentity(gtin="4006381333931"),
                variant_attributes={"regional_version": "Global"},
            ),
            "alternative_candidate",
            "insufficient",
        ),
        (
            offer(
                "amazon-bundle",
                identity=ProductIdentity(brand="Acme", model="X1"),
                variant_attributes={"capacity": "256 GB", "bundle": "headphones only"},
            ),
            offer(
                "ebay-bundle",
                identity=ProductIdentity(brand="Acme", model="X1"),
                variant_attributes={"capacity": "256 GB", "bundle": "headphones + case"},
            ),
            "alternative_candidate",
            "insufficient",
        ),
        (
            offer(
                "amazon-missing-variant",
                identity=ProductIdentity(brand="Acme", model="X1"),
                variant_attributes={"capacity": "256 GB", "condition": "new"},
            ),
            offer(
                "ebay-missing-variant",
                identity=ProductIdentity(brand="Acme", model="X1"),
                variant_attributes={"capacity": "256 GB"},
            ),
            "alternative_candidate",
            "insufficient",
        ),
        (
            offer("amazon-local", offer_id="local-123"),
            offer("ebay-local", offer_id="local-123"),
            "alternative_candidate",
            "insufficient",
        ),
    ],
    ids=[
        "identifier",
        "all-material-attributes",
        "title-only",
        "region",
        "bundle",
        "missing-variant",
        "local-id",
    ],
)
def test_identity_matcher_uses_only_authoritative_identity_evidence(
    reference: LandedCost,
    candidate: LandedCost,
    decision: str,
    basis: str,
) -> None:
    evidence = match_offer_identity(reference, candidate)

    assert evidence.decision == decision
    assert evidence.basis == basis
    if decision == "alternative_candidate":
        assert evidence.explanation


def test_exact_mode_keeps_alternatives_out_of_ranking_and_comparison() -> None:
    plan = ShoppingPlan(category="headphones", mode="exact_offer_comparison")
    result = decision_engine(
        plan,
        None,
        [
            offer(
                "amazon-match-expensive",
                identity=ProductIdentity(gtin="4006381333931"),
                landed_cny=900,
            ),
            offer(
                "ebay-match-cheap",
                identity=ProductIdentity(gtin="4006381333931"),
                landed_cny=850,
            ),
            offer("amazon-title-alternative", landed_cny=100),
        ],
    )

    assert result.mode == "exact_offer_comparison"
    assert [item.item_id for item in result.matching_offers] == [
        "amazon-match-expensive",
        "ebay-match-cheap",
    ]
    assert [item.item_id for item in result.alternative_candidates] == ["amazon-title-alternative"]
    assert [item.item_id for item in result.recommendations] == [
        "ebay-match-cheap",
        "amazon-match-expensive",
    ]
    assert [item.item_id for item in result.matching_offers] == [
        "amazon-match-expensive",
        "ebay-match-cheap",
    ]
    assert all(item.item_id != "amazon-title-alternative" for item in result.recommendations)


def test_product_research_retains_different_products_as_recommendations() -> None:
    result = decision_engine(
        ShoppingPlan(category="headphones", mode="product_research"),
        None,
        [offer("amazon-one", title="Acme X1"), offer("ebay-two", title="Other Y2", landed_cny=900)],
    )

    assert result.mode == "product_research"
    assert [item.item_id for item in result.recommendations] == ["amazon-one", "ebay-two"]
    assert result.alternative_candidates == []
    assert all(item.identity_evidence.decision == "not_required" for item in result.matching_offers)


@pytest.mark.asyncio
async def test_exact_summary_distinguishes_constraint_no_match_from_identity_no_match(
    tmp_path,
) -> None:
    classification = classify_exact_offers(
        [
            offer("amazon-match", identity=ProductIdentity(gtin="4006381333931")),
            offer("ebay-match", identity=ProductIdentity(gtin="4006381333931")),
            offer("amazon-alternative"),
        ]
    )
    picks = ItemPickerOutput(
        mode="exact_offer_comparison",
        recommendations=[],
        matching_offers=classification.matching_offers,
        alternative_candidates=classification.alternative_candidates,
        rejected_count=0,
    )

    with thread_scope("test-summary", tmp_path):
        result = await shopping_summary(picks, classification.matching_offers)

    assert "没有满足全部硬性条件" in result.final_answer
    assert "没有 Identity Evidence 充分的 Matching Offer" not in result.final_answer
