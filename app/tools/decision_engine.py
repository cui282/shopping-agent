from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.schemas import (
    ConstraintEvaluation,
    ConstraintEvidence,
    ConstraintExclusion,
    ConstraintRelaxationSuggestion,
    HardConstraint,
    ItemPickerOutput,
    LandedCost,
    Recommendation,
    RememberedPreference,
    ShoppingPlan,
    UnverifiedCandidate,
    WorkingAssumption,
)

_NUMERIC_VALUE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+|公斤|克|毫升|厘米|英寸|寸|小时)?")

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "material": ("material", "materials", "材质", "材料"),
    "color": ("color", "colour", "颜色", "色系"),
    "style": ("style", "风格"),
    "weight_kg": ("weight_kg", "weight", "重量", "净重"),
    "storage_gb": ("storage_gb", "storage", "memory", "内存", "存储"),
    "capacity": ("capacity", "容量"),
    "display_inch": ("display_inch", "display", "screen", "屏幕", "尺寸"),
    "battery_hours": ("battery_hours", "battery", "续航", "电池续航"),
    "specification": ("specification", "spec", "规格", "尺寸", "型号规格"),
}


def _normal_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value)).strip().lower()


def _mapping_values(offer: LandedCost, field: str) -> list[ConstraintEvidence]:
    aliases = {_normal_text(alias) for alias in _FIELD_ALIASES.get(field, (field,))}
    evidence: list[ConstraintEvidence] = []
    for mapping_name, values in (
        ("variant_attributes", offer.variant_attributes),
        ("attributes", offer.attributes),
    ):
        for key, value in values.items():
            if _normal_text(key) in aliases and value is not None:
                evidence.append(
                    ConstraintEvidence(
                        field_path=f"{mapping_name}.{key}",
                        value=value if isinstance(value, (str, int, float, bool)) else str(value),
                        source="product_evidence",
                    )
                )
    return evidence


def _facts_for_constraint(
    offer: LandedCost, constraint: HardConstraint
) -> list[ConstraintEvidence]:
    if constraint.field == "budget_cny":
        return [
            ConstraintEvidence(
                field_path="landed_cny",
                value=offer.landed_cny,
                source="computed",
            )
        ]
    return _mapping_values(offer, constraint.field)


def _numeric(value: object, unit: str | None) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    match = _NUMERIC_VALUE.match(value)
    if match is None:
        return None
    number = float(match.group(1))
    source_unit = (match.group(2) or "").lower()
    if unit == "kg" and source_unit in {"g", "克"}:
        number /= 1000
    elif unit == "gb" and source_unit in {"tb", "t"}:
        number *= 1024
    return number if math.isfinite(number) else None


def _evidence_text(evidence: list[ConstraintEvidence]) -> str:
    return ", ".join(f"{item.field_path}={item.value}" for item in evidence)


def _unknown(
    constraint: HardConstraint, evidence: list[ConstraintEvidence]
) -> ConstraintEvaluation:
    missing = constraint.label
    return ConstraintEvaluation(
        constraint=constraint,
        status="unknown",
        reason_code="missing_product_evidence",
        explanation=f"无法从 Product Evidence 验证{missing}。",
        evidence=evidence,
    )


def _evaluate_numeric(
    constraint: HardConstraint, evidence: list[ConstraintEvidence]
) -> ConstraintEvaluation:
    expected = _numeric(constraint.value, constraint.unit)
    actuals = [_numeric(item.value, constraint.unit) for item in evidence]
    actuals = [value for value in actuals if value is not None]
    if expected is None or not actuals:
        return _unknown(constraint, evidence)

    actual = actuals[0]
    if constraint.operator == "lte":
        satisfied = any(value <= expected for value in actuals)
        satisfied_code = (
            "budget_within_limit" if constraint.kind == "budget" else "value_within_max"
        )
    elif constraint.operator == "gte":
        satisfied = any(value >= expected for value in actuals)
        satisfied_code = "value_meets_minimum"
    elif constraint.operator == "equals":
        satisfied = any(value == expected for value in actuals)
        satisfied_code = "value_matches"
    elif constraint.operator == "not_equals":
        satisfied = all(value != expected for value in actuals)
        satisfied_code = "value_differs"
    else:
        return _unknown(constraint, evidence)

    if satisfied:
        return ConstraintEvaluation(
            constraint=constraint,
            status="satisfied",
            reason_code=satisfied_code,
            explanation=f"{constraint.label}，证据值为 {actual:g}{constraint.unit or ''}。",
            evidence=evidence,
        )
    violated_code = "budget_exceeded" if constraint.kind == "budget" else "value_out_of_range"
    return ConstraintEvaluation(
        constraint=constraint,
        status="violated",
        reason_code=violated_code,
        explanation=f"{constraint.label}，证据值为 {actual:g}{constraint.unit or ''}。",
        evidence=evidence,
    )


def _evaluate_text(
    offer: LandedCost,
    constraint: HardConstraint,
    evidence: list[ConstraintEvidence],
) -> ConstraintEvaluation:
    expected = _normal_text(constraint.value)
    values = [_normal_text(item.value) for item in evidence if item.value is not None]
    if not values and constraint.field in {"material", "color", "style", "specification"}:
        title = _normal_text(offer.title)
        if constraint.operator in {"contains", "not_contains"} and expected in title:
            evidence = [
                ConstraintEvidence(
                    field_path="title",
                    value=offer.title,
                    source="product_evidence",
                )
            ]
            values = [title]
    if not values:
        return _unknown(constraint, evidence)

    contains = any(expected in value for value in values)
    if constraint.operator == "contains":
        satisfied = contains
        satisfied_code = "attribute_matches"
        violated_code = "attribute_conflicts"
    elif constraint.operator == "not_contains":
        satisfied = not contains
        satisfied_code = "prohibited_attribute_absent"
        violated_code = "prohibited_attribute_present"
    elif constraint.operator == "equals":
        satisfied = any(value == expected for value in values)
        satisfied_code = "attribute_matches"
        violated_code = "attribute_conflicts"
    elif constraint.operator == "not_equals":
        satisfied = all(value != expected for value in values)
        satisfied_code = "attribute_differs"
        violated_code = "attribute_conflicts"
    else:
        return _unknown(constraint, evidence)

    status = "satisfied" if satisfied else "violated"
    return ConstraintEvaluation(
        constraint=constraint,
        status=status,
        reason_code=satisfied_code if satisfied else violated_code,
        explanation=(f"{constraint.label}，证据为 {_evidence_text(evidence)}。"),
        evidence=evidence,
    )


def evaluate_constraint(offer: LandedCost, constraint: HardConstraint) -> ConstraintEvaluation:
    """Evaluate one Hard Constraint using only normalized Product Evidence."""

    evidence = _facts_for_constraint(offer, constraint)
    if constraint.field == "budget_cny" or constraint.operator in {"lte", "gte"}:
        return _evaluate_numeric(constraint, evidence)
    if constraint.operator in {"equals", "not_equals"} and any(
        _numeric(item.value, constraint.unit) is not None for item in evidence
    ):
        return _evaluate_numeric(constraint, evidence)
    return _evaluate_text(offer, constraint, evidence)


def _default_assumptions() -> list[WorkingAssumption]:
    return [
        WorkingAssumption(
            code="optional_color_unspecified",
            field="color",
            value="不设限",
            reason="请求未指定颜色，保留 Product Evidence 中可验证的各色候选。",
        ),
        WorkingAssumption(
            code="optional_style_unspecified",
            field="style",
            value="不设限",
            reason="请求未指定风格，不把缺省风格升级为 Blocking Ambiguity。",
        ),
    ]


def _remembered_model(
    value: RememberedPreference | Mapping[str, Any] | None,
) -> RememberedPreference:
    if isinstance(value, RememberedPreference):
        return value
    if value is None:
        return RememberedPreference()
    return RememberedPreference.model_validate(
        {field: value.get(field, []) for field in RememberedPreference.model_fields}
    )


def _recommendation_reason(offer: LandedCost, remembered: RememberedPreference) -> str:
    del remembered  # Remembered Preference is a soft signal and never changes eligibility.
    return f"全部硬性条件均已由 Product Evidence 验证；到手约¥{offer.landed_cny:.0f}，预计{offer.eta_days}天。"


def _unverified_reason(evaluations: list[ConstraintEvaluation]) -> str:
    labels = [item.constraint.label for item in evaluations if item.status == "unknown"]
    return "缺少可验证证据：" + "、".join(labels)


def _relaxations(exclusions: list[ConstraintExclusion]) -> list[ConstraintRelaxationSuggestion]:
    suggestions: list[ConstraintRelaxationSuggestion] = []
    seen: set[str] = set()
    for exclusion in exclusions:
        for evaluation in exclusion.violated_constraints:
            constraint = evaluation.constraint
            if constraint.id in seen:
                continue
            seen.add(constraint.id)
            suggestions.append(
                ConstraintRelaxationSuggestion(
                    constraint=constraint,
                    suggestion=f"如需扩大候选，可在你确认后放宽“{constraint.label}”；当前任务未自动放宽。",
                )
            )
    return suggestions


def decision_engine(
    intent: ShoppingPlan,
    remembered_preferences: RememberedPreference | Mapping[str, Any] | None,
    normalized_offers: Sequence[LandedCost],
    *,
    max_items: int = 3,
) -> ItemPickerOutput:
    """Deterministically classify normalized offers against the current task intent."""

    remembered = _remembered_model(remembered_preferences)
    assumptions = intent.working_assumptions or _default_assumptions()
    recommendations: list[tuple[LandedCost, list[ConstraintEvaluation]]] = []
    unverified: list[UnverifiedCandidate] = []
    exclusions: list[ConstraintExclusion] = []

    for offer in normalized_offers:
        evaluations = [
            evaluate_constraint(offer, constraint) for constraint in intent.hard_constraints
        ]
        violated = [item for item in evaluations if item.status == "violated"]
        unknown = [item for item in evaluations if item.status == "unknown"]
        if unknown:
            unverified.append(
                UnverifiedCandidate(
                    **offer.model_dump(),
                    reason=_unverified_reason(evaluations),
                    constraint_evaluations=evaluations,
                )
            )
        elif violated:
            exclusions.append(
                ConstraintExclusion(
                    item_id=offer.item_id,
                    platform=offer.platform,
                    title=offer.title,
                    violated_count=len(violated),
                    violated_constraints=violated,
                )
            )
        else:
            recommendations.append((offer, evaluations))

    recommendations.sort(
        key=lambda item: (
            item[0].landed_cny,
            -(item[0].rating if item[0].rating is not None else 0),
            -(item[0].sales if item[0].sales is not None else 0),
        )
    )
    limit = max(1, min(max_items, 3))
    picks = [
        Recommendation(
            **offer.model_dump(),
            reason=_recommendation_reason(offer, remembered),
            rank=rank,
            constraint_evaluations=evaluations,
        )
        for rank, (offer, evaluations) in enumerate(recommendations[:limit], start=1)
    ]
    return ItemPickerOutput(
        recommendations=picks,
        unverified_candidates=unverified,
        exclusions=exclusions,
        working_assumptions=assumptions,
        relaxation_suggestions=_relaxations(exclusions),
        match_status="matched" if picks else "no_match",
        rejected_count=len(exclusions),
    )
