from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.schemas import (
    AlternativeCandidate,
    ConstraintEvaluation,
    ConstraintEvidence,
    ConstraintExclusion,
    ConstraintRelaxationSuggestion,
    HardConstraint,
    IdentityEvidence,
    ItemPickerOutput,
    LandedCost,
    RankingDimension,
    RankingProfile,
    RankingScoreBreakdown,
    Recommendation,
    RememberedPreference,
    ShoppingPlan,
    UnverifiedCandidate,
    WorkingAssumption,
)
from app.tools.identity_matcher import classify_exact_offers

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


_RANKING_LABELS: dict[RankingDimension, str] = {
    "landed_cost": "到手价",
    "preference_match": "偏好匹配",
    "evidence_quality": "证据质量",
    "delivery_time": "配送时效",
}


def _preference_terms(intent: ShoppingPlan, remembered: RememberedPreference) -> list[str]:
    values = [
        *intent.style_preferences,
        *intent.soft_preferences,
        *remembered.material_preferences,
        *remembered.style_preferences,
        *remembered.soft_preferences,
    ]
    return list(dict.fromkeys(value for value in values if value.strip()))


def _product_evidence_text(offer: LandedCost) -> list[str]:
    values = [offer.title]
    values.extend(str(value) for value in offer.attributes.values() if value is not None)
    values.extend(str(value) for value in offer.variant_attributes.values() if value is not None)
    return [_normal_text(value) for value in values]


def _preference_match_score(
    offer: LandedCost, intent: ShoppingPlan, remembered: RememberedPreference
) -> float:
    terms = _preference_terms(intent, remembered)
    if not terms:
        return 0.5
    evidence = _product_evidence_text(offer)
    matched = sum(
        1
        for term in terms
        if any(_normal_text(term) in evidence_value for evidence_value in evidence)
    )
    return round(matched / len(terms), 4)


def _evidence_quality_score(offer: LandedCost) -> float:
    checks = (
        offer.source in {"live", "fixture", "curated"},
        offer.provenance is not None,
        bool(offer.product_url),
        bool(offer.offer_id or any(offer.identity.model_dump().values())),
        bool(offer.variant_attributes),
        offer.availability is not None,
        offer.retrieved_at is not None,
        offer.rating is not None,
        offer.sales is not None,
    )
    return round(sum(checks) / len(checks), 4)


def _relative_lower_is_better(value: float, values: list[float]) -> float:
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return 1.0
    return round(1 - ((value - minimum) / (maximum - minimum)), 4)


def _score_breakdown(
    offer: LandedCost,
    profile: RankingProfile,
    remembered: RememberedPreference,
    intent: ShoppingPlan,
    eligible: list[LandedCost],
) -> RankingScoreBreakdown:
    landed_values = [item.landed_cny for item in eligible]
    eta_values = [float(item.eta_days) for item in eligible]
    return RankingScoreBreakdown(
        priority_order=profile.priority_order,
        landed_cost_cny=offer.landed_cny,
        landed_cost_score=_relative_lower_is_better(offer.landed_cny, landed_values),
        preference_match_score=_preference_match_score(offer, intent, remembered),
        evidence_quality_score=_evidence_quality_score(offer),
        delivery_time_days=offer.eta_days,
        delivery_time_score=_relative_lower_is_better(float(offer.eta_days), eta_values),
    )


def _recommendation_reason(offer: LandedCost, profile: RankingProfile) -> str:
    priorities = "、".join(_RANKING_LABELS[dimension] for dimension in profile.priority_order)
    return (
        f"全部硬性条件均已通过 Product Evidence 或确定性计算验证；按{priorities}排序。"
        f"商品价为 {offer.currency} {offer.price:.2f}（折合 CNY {offer.price_cny:.2f}），"
        f"运费 ¥{offer.shipping_cny:.2f}（估算，来源：{offer.shipping_estimate.source}），"
        f"关税 ¥{offer.duty_cny:.2f}（估算，来源：{offer.duty_estimate.source}），"
        f"到手约 ¥{offer.landed_cny:.2f}；配送 {offer.eta_days} 天"
        f"（估算，来源：{offer.delivery_estimate.source}）。"
    )


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
    if intent.mode == "exact_offer_comparison":
        classification = classify_exact_offers(normalized_offers)
        matching_offers = classification.matching_offers
        alternative_candidates = classification.alternative_candidates
    else:
        matching_offers = [
            offer.model_copy(
                update={
                    "identity_evidence": IdentityEvidence(
                        decision="not_required",
                        basis="not_required",
                        explanation="Product Research 可以比较不同 Product Variant，不要求同款证明。",
                    )
                }
            )
            for offer in normalized_offers
        ]
        alternative_candidates: list[AlternativeCandidate] = []
    recommendations: list[tuple[LandedCost, list[ConstraintEvaluation]]] = []
    unverified: list[UnverifiedCandidate] = []
    exclusions: list[ConstraintExclusion] = []

    for offer in matching_offers:
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

    profile = intent.ranking_profile
    eligible_offers = [offer for offer, _ in recommendations]
    scored = [
        (offer, evaluations, _score_breakdown(offer, profile, remembered, intent, eligible_offers))
        for offer, evaluations in recommendations
    ]

    def sort_key(
        item: tuple[LandedCost, list[ConstraintEvaluation], RankingScoreBreakdown],
    ) -> tuple[float | str, ...]:
        offer, _, breakdown = item
        primary: list[float] = []
        for dimension in profile.priority_order:
            if dimension == "landed_cost":
                primary.append(offer.landed_cny)
            elif dimension == "delivery_time":
                primary.append(float(offer.eta_days))
            elif dimension == "preference_match":
                primary.append(-breakdown.preference_match_score)
            else:
                primary.append(-breakdown.evidence_quality_score)
        return (*primary, offer.landed_cny, float(offer.eta_days), offer.platform, offer.item_id)

    scored.sort(key=sort_key)
    limit = max(1, min(max_items, 3))
    picks = [
        Recommendation(
            **offer.model_dump(),
            reason=_recommendation_reason(offer, profile),
            rank=rank,
            constraint_evaluations=evaluations,
            score_breakdown=breakdown,
            offer_kind=(
                "matching_offer"
                if intent.mode == "exact_offer_comparison"
                else "research_candidate"
            ),
        )
        for rank, (offer, evaluations, breakdown) in enumerate(scored[:limit], start=1)
    ]
    return ItemPickerOutput(
        mode=intent.mode,
        recommendations=picks,
        matching_offers=matching_offers,
        alternative_candidates=alternative_candidates,
        unverified_candidates=unverified,
        exclusions=exclusions,
        working_assumptions=assumptions,
        relaxation_suggestions=_relaxations(exclusions),
        match_status="matched" if picks else "no_match",
        rejected_count=len(exclusions),
        ranking_profile=profile,
    )
