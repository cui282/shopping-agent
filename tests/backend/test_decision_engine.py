from __future__ import annotations

import pytest

from app.memory.injector import extract_preferences
from app.schemas import (
    HardConstraint,
    LandedCost,
    OfferProvenance,
    RememberedPreference,
    ShoppingPlan,
)
from app.tools.decision_engine import decision_engine
from app.tools.planner import planner


def landed_offer(
    item_id: str,
    *,
    title: str = "通勤耳机",
    landed_cny: float = 888,
    attributes: dict[str, object] | None = None,
) -> LandedCost:
    return LandedCost(
        item_id=item_id,
        platform="amazon",
        title=title,
        price=100,
        currency="USD",
        price_cny=718,
        shipping_cny=85,
        duty_cny=85,
        landed_cny=landed_cny,
        eta_days=12,
        duty_tier="标准",
        attributes=attributes or {},
        source="live",
        provenance=OfferProvenance(
            kind="marketplace_gateway",
            provider="test-gateway",
            upstream_source="test-catalog",
        ),
    )


@pytest.mark.asyncio
async def test_planner_normalizes_chinese_negative_material_and_spec_constraints() -> None:
    plan = await planner("预算 1200 元，找耳机，不要塑料的，重量不超过 1kg")

    assert plan.budget_cny == 1200
    assert [
        (item.field, item.operator, item.value, item.unit) for item in plan.hard_constraints
    ] == [
        ("budget_cny", "lte", 1200, "CNY"),
        ("material", "not_contains", "塑料", None),
        ("weight_kg", "lte", 1, "kg"),
    ]
    assert {assumption.field for assumption in plan.working_assumptions} == {"color", "style"}
    assert plan.material_preferences == ["不含塑料"]


@pytest.mark.asyncio
async def test_planner_distinguishes_positive_material_and_optional_values() -> None:
    plan = await planner("材质为金属，颜色不限，风格不限")

    assert [
        (item.kind, item.field, item.operator, item.value) for item in plan.hard_constraints
    ] == [
        ("material", "material", "contains", "金属"),
    ]
    assert {assumption.field for assumption in plan.working_assumptions} == {"color", "style"}

    negative_color = await planner("找耳机，不要黑色的")
    assert [
        (item.kind, item.field, item.operator, item.value)
        for item in negative_color.hard_constraints
    ] == [("attribute", "color", "not_contains", "黑色")]


def test_negative_material_preference_is_normalized_before_memory_storage() -> None:
    plan = ShoppingPlan(category="耳机")

    assert extract_preferences("找耳机，不要塑料的", plan)["avoid"] == ["塑料"]


def test_decision_engine_recommends_only_fully_satisfied_offers_with_evidence() -> None:
    intent = ShoppingPlan(
        category="耳机",
        hard_constraints=[
            HardConstraint(
                id="material_not_contains_plastic",
                kind="material",
                field="material",
                operator="not_contains",
                value="塑料",
                label="材质不含塑料",
            )
        ],
    )
    offer = landed_offer("verified", attributes={"material": "金属与织物"})

    result = decision_engine(intent, RememberedPreference(), [offer])

    assert [item.item_id for item in result.recommendations] == ["verified"]
    assert result.recommendations[0].constraint_evaluations[0].status == "satisfied"
    assert result.recommendations[0].constraint_evaluations[0].evidence[0].field_path == (
        "attributes.material"
    )
    assert result.exclusions == []
    assert result.unverified_candidates == []
    assert result.match_status == "matched"


def test_remembered_preference_does_not_become_a_hard_constraint() -> None:
    intent = ShoppingPlan(category="耳机")
    offer = landed_offer("remembered-only", attributes={})

    result = decision_engine(
        intent,
        RememberedPreference(material_preferences=["不含皮革"]),
        [offer],
    )

    assert [item.item_id for item in result.recommendations] == ["remembered-only"]
    assert result.unverified_candidates == []
    assert result.exclusions == []


def test_unknown_evidence_is_unverified_and_never_a_recommendation() -> None:
    intent = ShoppingPlan(
        category="耳机",
        hard_constraints=[
            HardConstraint(
                id="material_not_contains_plastic",
                kind="material",
                field="material",
                operator="not_contains",
                value="塑料",
                label="材质不含塑料",
            )
        ],
    )

    result = decision_engine(intent, RememberedPreference(), [landed_offer("unknown")])

    assert result.recommendations == []
    assert [item.item_id for item in result.unverified_candidates] == ["unknown"]
    assert result.unverified_candidates[0].constraint_evaluations[0].status == "unknown"
    assert result.unverified_candidates[0].constraint_evaluations[0].reason_code == (
        "missing_product_evidence"
    )
    assert result.exclusions == []
    assert result.match_status == "no_match"


def test_mixed_violation_and_unknown_keeps_all_evaluations_unverified() -> None:
    intent = ShoppingPlan(
        category="耳机",
        hard_constraints=[
            HardConstraint(
                id="budget_cny_lte_500",
                kind="budget",
                field="budget_cny",
                operator="lte",
                value=500,
                unit="CNY",
                label="到手价不超过500元",
            ),
            HardConstraint(
                id="material_not_contains_plastic",
                kind="material",
                field="material",
                operator="not_contains",
                value="塑料",
                label="材质不含塑料",
            ),
        ],
    )

    result = decision_engine(
        intent, RememberedPreference(), [landed_offer("mixed", landed_cny=800)]
    )

    assert result.exclusions == []
    assert len(result.unverified_candidates) == 1
    assert [item.status for item in result.unverified_candidates[0].constraint_evaluations] == [
        "violated",
        "unknown",
    ]


def test_violations_are_excluded_with_machine_reasons_and_no_silent_relaxation() -> None:
    intent = ShoppingPlan(
        category="耳机",
        hard_constraints=[
            HardConstraint(
                id="budget_cny_lte_500",
                kind="budget",
                field="budget_cny",
                operator="lte",
                value=500,
                unit="CNY",
                label="到手价不超过 500 元",
            ),
            HardConstraint(
                id="material_not_contains_plastic",
                kind="material",
                field="material",
                operator="not_contains",
                value="塑料",
                label="材质不含塑料",
            ),
        ],
    )
    offer = landed_offer("violated", landed_cny=800, attributes={"material": "塑料"})

    result = decision_engine(intent, RememberedPreference(), [offer])

    assert result.recommendations == []
    assert result.unverified_candidates == []
    assert result.exclusions[0].item_id == "violated"
    assert result.exclusions[0].violated_count == 2
    assert [reason.reason_code for reason in result.exclusions[0].violated_constraints] == [
        "budget_exceeded",
        "prohibited_attribute_present",
    ]
    assert result.relaxation_suggestions
    assert all(item.requires_confirmation for item in result.relaxation_suggestions)
    assert result.match_status == "no_match"
