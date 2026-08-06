from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypeAliasType

Platform = Literal["amazon", "shopee", "aliexpress", "ebay"]
ProviderSource = Literal["live", "curated", "fixture", "computed"]
DataMode = Literal["live", "sandbox", "mixed"]
ResearchMode = Literal["product_research", "exact_offer_comparison"]
JsonValue = TypeAliasType(
    "JsonValue",
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"],
)
IdentityEvidenceBasis = Literal[
    "identifier", "material_variant_attributes", "insufficient", "not_required"
]
ResultKind = Literal["live", "sandbox", "partial"]
ReportFormat = Literal["markdown", "json", "pdf"]
ProviderFailureReason = Literal[
    "not_configured",
    "request_failed",
    "empty_response",
    "sandbox_forbidden",
]
OfferLinkKind = Literal["product_detail", "marketplace_search"]
ConstraintStatus = Literal["satisfied", "violated", "unknown"]
ConstraintKind = Literal["budget", "material", "attribute", "specification"]
ConstraintOperator = Literal[
    "lte",
    "gte",
    "equals",
    "not_equals",
    "contains",
    "not_contains",
]
ConstraintEvidenceSource = Literal["product_evidence", "computed"]
RankingDimension = Literal[
    "landed_cost",
    "preference_match",
    "evidence_quality",
    "delivery_time",
]
PreferenceField = Literal[
    "material_preferences",
    "style_preferences",
    "soft_preferences",
    "avoid",
]
MemoryAction = Literal["remember", "forget"]
PreferenceDecisionStatus = Literal["applied", "ignored", "overridden"]
PreferenceDecisionSource = Literal["current_request", "remembered_preference"]
PreferenceDurability = Literal["local_evaluation", "durable"]
CalculationExclusionReason = Literal["unsupported_currency", "invalid_amount"]
ClarificationField = Literal["mode", "product_variant", "destination"]
ClarificationReasonCode = Literal[
    "mode_ambiguous",
    "product_variant_ambiguous",
    "destination_ambiguous",
]
EventName = Literal[
    "session_created",
    "intent_resolved",
    "assistant_call",
    "tool_start",
    "tool_end",
    "fork",
    "report_generated",
    "task_result",
    "task_cancelled",
    "clarification_required",
    "clarification_resolved",
    "error",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TaskRequest(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    user_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    upload_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class TaskStarted(StrictModel):
    status: Literal["started"] = "started"
    thread_id: str


class ProviderMetadata(StrictModel):
    source: ProviderSource
    provider: str
    status: Literal["ok", "degraded", "unavailable"] = "ok"
    fallback_reason: str | None = None
    failure_reason: ProviderFailureReason | None = None


class ProductIdentity(StrictModel):
    gtin: str | None = None
    mpn: str | None = None
    brand: str | None = None
    model: str | None = None


class IdentityEvidence(StrictModel):
    decision: Literal["matching_offer", "alternative_candidate", "not_required"] = "not_required"
    basis: IdentityEvidenceBasis = "not_required"
    matched_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)
    explanation: str = "Product Research 不要求跨平台同款证明。"


class OfferProvenance(StrictModel):
    kind: Literal["marketplace_gateway", "sandbox_fixture"]
    provider: str | None = None
    upstream_source: str | None = None


class MarketplaceDemand(StrictModel):
    platform: Platform
    query: str = Field(min_length=1, max_length=4000)


class ForkEventData(StrictModel):
    sub_thread_id: str = Field(pattern=r"^sub-[0-9a-f]{8}$")
    platform: Platform
    demand: MarketplaceDemand
    data_mode: DataMode = "live"

    @model_validator(mode="after")
    def demand_matches_platform(self) -> ForkEventData:
        if self.demand.platform != self.platform:
            raise ValueError("fork platform must match demand platform")
        return self


class SessionCreatedEventData(StrictModel):
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    reference_images: list[dict[str, Any]]
    data_mode: DataMode = "live"


class ToolStartEventData(StrictModel):
    tool_name: str = Field(min_length=1)
    args: dict[str, Any]
    data_mode: DataMode = "live"


class AssistantCallEventData(StrictModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    step: str = Field(min_length=1)
    data_mode: DataMode = "live"


class ToolEndEventData(StrictModel):
    tool_name: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)
    outcome: Literal["success", "degraded", "failure"]
    source: ProviderSource
    provider: str = Field(min_length=1)
    status: Literal["ok", "degraded", "unavailable"]
    fallback_reason: str | None = None
    failure_reason: ProviderFailureReason | None = None
    data_mode: DataMode = "live"

    @model_validator(mode="after")
    def outcome_matches_status(self) -> ToolEndEventData:
        expected = {"success": "ok", "degraded": "degraded", "failure": "unavailable"}
        if self.status != expected[self.outcome]:
            raise ValueError("tool outcome must match provider status")
        return self


class TaskCancelledEventData(StrictModel):
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    data_mode: DataMode = "live"


class ErrorEventData(TaskCancelledEventData):
    code: str = Field(min_length=1)


class ClarificationPrompt(StrictModel):
    field: ClarificationField
    reason_code: ClarificationReasonCode
    question: str = Field(min_length=1, max_length=4000)


class ClarificationRequiredEventData(ClarificationPrompt):
    data_mode: DataMode = "live"


class ClarificationResolvedEventData(StrictModel):
    field: ClarificationField
    reason_code: ClarificationReasonCode
    response: str = Field(min_length=1, max_length=4000)
    resolved_value: str | None = Field(default=None, min_length=1, max_length=4000)
    data_mode: DataMode = "live"


class ClarificationCommand(StrictModel):
    response: str = Field(max_length=4000)

    @field_validator("response", mode="before")
    @classmethod
    def normalize_response(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ClarificationCommandResponse(StrictModel):
    status: Literal["resumed"] = "resumed"
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    field: ClarificationField
    idempotent: bool = False


class MonitorEvent(StrictModel):
    type: Literal["monitor_event"] = "monitor_event"
    event_id: str = Field(pattern=r"^evt-[0-9a-f]{32}$")
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    run_id: str = Field(default="legacy", pattern=r"^(?:legacy|[0-9a-f]{32})$")
    sequence: int = Field(ge=1)
    event: EventName
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    @model_validator(mode="before")
    @classmethod
    def validate_typed_event_data(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        event = value.get("event")
        models = {
            "session_created": SessionCreatedEventData,
            "intent_resolved": IntentResolvedEventData,
            "assistant_call": AssistantCallEventData,
            "tool_start": ToolStartEventData,
            "tool_end": ToolEndEventData,
            "fork": ForkEventData,
            "report_generated": ReportGeneratedEventData,
            "task_cancelled": TaskCancelledEventData,
            "clarification_required": ClarificationRequiredEventData,
            "clarification_resolved": ClarificationResolvedEventData,
            "error": ErrorEventData,
        }
        model = models.get(event)
        if event == "task_result":
            validated = ShoppingSummaryOutput.model_validate(value.get("data", {}))
            return {**value, "data": validated.model_dump(mode="json")}
        if model is None:
            return value
        validated = model.model_validate(value.get("data", {}))
        return {**value, "data": validated.model_dump(mode="json")}


class HardConstraint(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    kind: ConstraintKind
    field: str = Field(min_length=1)
    operator: ConstraintOperator
    value: str | int | float
    unit: str | None = None
    label: str = Field(min_length=1)


class ConstraintRelaxationChange(StrictModel):
    constraint_id: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    replacement: HardConstraint | None = None
    reason: str = Field(
        default="购物者明确确认后修改这项 Hard Constraint。", min_length=1, max_length=4000
    )


class ConstraintRelaxation(StrictModel):
    constraint_id: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    previous: HardConstraint
    replacement: HardConstraint | None = None
    action: Literal["removed", "replaced"]
    reason: str = Field(min_length=1, max_length=4000)


class RankingProfile(StrictModel):
    priority_order: list[RankingDimension] = Field(
        default_factory=lambda: [
            "landed_cost",
            "preference_match",
            "evidence_quality",
            "delivery_time",
        ],
        min_length=4,
        max_length=4,
    )
    explicit: bool = False

    @model_validator(mode="after")
    def require_each_dimension_once(self) -> RankingProfile:
        expected = {"landed_cost", "preference_match", "evidence_quality", "delivery_time"}
        if len(set(self.priority_order)) != len(expected) or set(self.priority_order) != expected:
            raise ValueError("ranking profile must contain each ranking dimension exactly once")
        return self


class RankingScoreBreakdown(StrictModel):
    priority_order: list[RankingDimension] = Field(min_length=4, max_length=4)
    landed_cost_cny: float = Field(ge=0)
    landed_cost_score: float = Field(ge=0, le=1)
    preference_match_score: float = Field(ge=0, le=1)
    evidence_quality_score: float = Field(ge=0, le=1)
    delivery_time_days: int = Field(ge=0)
    delivery_time_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_each_dimension_once(self) -> RankingScoreBreakdown:
        expected = {"landed_cost", "preference_match", "evidence_quality", "delivery_time"}
        if len(set(self.priority_order)) != len(expected) or set(self.priority_order) != expected:
            raise ValueError("score breakdown must contain each ranking dimension exactly once")
        return self


class ExchangeRateProvenance(StrictModel):
    base_currency: Literal["CNY"] = "CNY"
    source: str = Field(default="unspecified", min_length=1)
    effective_date: str = Field(default="unspecified", min_length=1)
    calculation_basis: str = Field(default="original_amount * rate_to_cny", min_length=1)


class CalculationExclusion(StrictModel):
    item_id: str = Field(min_length=1)
    platform: Platform
    title: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    amount: float | None = None
    reason_code: CalculationExclusionReason
    reason: str = Field(min_length=1)


class EstimateDisclosure(StrictModel):
    estimated: bool = True
    source: str = Field(default="computed", min_length=1)
    calculation_basis: str = Field(default="not provided", min_length=1)


class WorkingAssumption(StrictModel):
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RememberedPreference(StrictModel):
    material_preferences: list[str] = Field(default_factory=list)
    style_preferences: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class MemoryCommand(StrictModel):
    action: MemoryAction
    field: PreferenceField
    values: list[str] = Field(min_length=1, max_length=20)
    scope: Literal["future_tasks"] = "future_tasks"

    @field_validator("values", mode="before")
    @classmethod
    def normalize_values(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        normalized = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(normalized))


class PreferenceDecision(StrictModel):
    field: PreferenceField
    value: str = Field(min_length=1)
    status: PreferenceDecisionStatus
    source: PreferenceDecisionSource
    reason: str = Field(min_length=1)


class TaskOverride(StrictModel):
    field: PreferenceField
    value: str = Field(min_length=1)
    overridden_values: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class RerunCommand(StrictModel):
    user_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$"
    )


class ConstraintRelaxationCommand(StrictModel):
    user_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    confirmed: bool = False
    constraint_ids: list[str] = Field(default_factory=list, max_length=20)
    changes: list[ConstraintRelaxationChange] = Field(default_factory=list, max_length=20)
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$"
    )

    @model_validator(mode="after")
    def normalize_constraint_ids(self) -> ConstraintRelaxationCommand:
        ids = [*self.constraint_ids, *(change.constraint_id for change in self.changes)]
        self.constraint_ids = list(dict.fromkeys(ids))
        for change in self.changes:
            if change.replacement is not None and change.replacement.id != change.constraint_id:
                raise ValueError("a relaxed constraint replacement must keep the original id")
        return self


class PreferenceBackendStatus(StrictModel):
    requested_backend: Literal["memory", "redis"]
    backend: Literal["memory", "redis"]
    durability: PreferenceDurability
    fallback_reason: str | None = None


class PreferenceResponse(StrictModel):
    user_id: str
    preferences: dict[str, list[str]] = Field(default_factory=dict)
    backend: PreferenceBackendStatus


class PreferenceDeleteResponse(StrictModel):
    status: Literal["deleted"] = "deleted"
    user_id: str
    backend: PreferenceBackendStatus


class ShoppingPlan(StrictModel):
    mode: ResearchMode = "product_research"
    budget_cny: float | None = None
    category: str
    material_preferences: list[str] = Field(default_factory=list)
    style_preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    destination: str = "中国大陆"
    ranking_profile: RankingProfile = Field(default_factory=RankingProfile)
    working_assumptions: list[WorkingAssumption] = Field(default_factory=list)
    source: ProviderSource = "computed"


class IntentResolvedEventData(StrictModel):
    resolved_query: str = Field(min_length=1, max_length=4000)
    resolved_intent: ShoppingPlan
    applied_preferences: RememberedPreference = Field(default_factory=RememberedPreference)
    task_overrides: list[TaskOverride] = Field(default_factory=list)
    constraint_relaxations: list[ConstraintRelaxation] = Field(default_factory=list)
    data_mode: DataMode = "live"


class SearchResult(StrictModel):
    title: str
    url: str | None = None
    snippet: str | None = None


class WebSearchOutput(StrictModel):
    query: str
    results: list[SearchResult]
    provider: ProviderMetadata


class Bestseller(StrictModel):
    name: str
    typical_price_cny: float | None = None
    why_popular: str


class AttributeDist(StrictModel):
    name: str
    distribution: dict[str, float]


class PriceTier(StrictModel):
    tier: Literal["budget", "mid", "premium"]
    range_cny: tuple[float, float]
    notes: str


class CategoryInsightOutput(StrictModel):
    category: str
    components: list[str]
    bestsellers: list[Bestseller]
    attributes: list[AttributeDist]
    price_tiers: list[PriceTier]
    confidence: float = Field(ge=0, le=1)
    provider: ProviderMetadata


class Candidate(StrictModel):
    item_id: str
    platform: Platform
    title: str
    price: float = Field(ge=0)
    currency: str
    rating: float | None = None
    sales: int | None = None
    image_url: str | None = None
    product_url: str | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    source: ProviderSource
    marketplace: Platform | None = None
    offer_id: str | None = None
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    variant_attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    availability: str | None = None
    retrieved_at: str | None = None
    provenance: OfferProvenance | None = None
    link_kind: OfferLinkKind | None = None
    identity_evidence: IdentityEvidence | None = None

    @model_validator(mode="after")
    def normalize_marketplace(self) -> Candidate:
        if self.marketplace is None:
            self.marketplace = self.platform
        elif self.marketplace != self.platform:
            raise ValueError("marketplace must match platform")
        return self


class ItemSearchOutput(StrictModel):
    platform: Platform
    candidates: list[Candidate]
    total_recall: int
    truncated: bool
    provider: ProviderMetadata


class PricePoint(StrictModel):
    item_id: str
    platform: Platform
    title: str
    price: float
    currency: str
    price_cny: float
    rating: float | None = None
    sales: int | None = None
    image_url: str | None = None
    product_url: str | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    source: ProviderSource
    note: str | None = None
    marketplace: Platform | None = None
    offer_id: str | None = None
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    variant_attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    availability: str | None = None
    retrieved_at: str | None = None
    provenance: OfferProvenance | None = None
    link_kind: OfferLinkKind | None = None
    identity_evidence: IdentityEvidence | None = None

    @model_validator(mode="after")
    def normalize_marketplace(self) -> PricePoint:
        if self.marketplace is None:
            self.marketplace = self.platform
        elif self.marketplace != self.platform:
            raise ValueError("marketplace must match platform")
        return self


class PriceCompareOutput(StrictModel):
    base_currency: Literal["CNY"] = "CNY"
    ranked: list[PricePoint]
    cheapest_per_platform: dict[str, PricePoint]
    rate_source: str
    rates_as_of: str
    exchange_rate: ExchangeRateProvenance = Field(default_factory=ExchangeRateProvenance)
    excluded_currencies: list[str] = Field(default_factory=list)
    calculation_exclusions: list[CalculationExclusion] = Field(default_factory=list)


class LandedCost(PricePoint):
    shipping_cny: float
    duty_cny: float
    landed_cny: float
    eta_days: int
    duty_tier: Literal["免征", "标准", "高税"]
    shipping_estimate: EstimateDisclosure = Field(default_factory=EstimateDisclosure)
    duty_estimate: EstimateDisclosure = Field(default_factory=EstimateDisclosure)
    delivery_estimate: EstimateDisclosure = Field(default_factory=EstimateDisclosure)


class ReportEvidence(StrictModel):
    """Stable evidence projection shared by the human-readable report renderers."""

    item_id: str
    platform: Platform
    marketplace: Platform
    offer_id: str | None = None
    title: str
    original_price: float
    original_currency: str
    price_cny: float | None = None
    shipping_cny: float | None = None
    duty_cny: float | None = None
    landed_cny: float | None = None
    eta_days: int | None = None
    rating: float | None = None
    sales: int | None = None
    image_url: str | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    identity_evidence: IdentityEvidence | None = None
    variant_attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    availability: str | None = None
    provenance: OfferProvenance | None = None
    source: ProviderSource
    retrieved_at: str | None = None
    link_kind: OfferLinkKind | None = None
    product_url: str | None = None


class ConstraintEvidence(StrictModel):
    field_path: str = Field(min_length=1)
    value: str | int | float | bool | None = None
    source: ConstraintEvidenceSource


class ConstraintEvaluation(StrictModel):
    constraint: HardConstraint
    status: ConstraintStatus
    reason_code: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    explanation: str = Field(min_length=1)
    evidence: list[ConstraintEvidence] = Field(default_factory=list)


class ConstraintExclusion(StrictModel):
    item_id: str
    platform: Platform
    title: str
    violated_count: int = Field(ge=1)
    violated_constraints: list[ConstraintEvaluation] = Field(min_length=1)


class ConstraintRelaxationSuggestion(StrictModel):
    constraint: HardConstraint
    suggestion: str = Field(min_length=1)
    requires_confirmation: bool = True


class UnverifiedCandidate(LandedCost):
    reason: str = Field(min_length=1)
    constraint_evaluations: list[ConstraintEvaluation] = Field(min_length=1)


class AlternativeCandidate(LandedCost):
    reason: str = Field(min_length=1)
    identity_evidence: IdentityEvidence = Field(
        default_factory=lambda: IdentityEvidence(
            decision="alternative_candidate",
            basis="insufficient",
            explanation="缺少足够的 Identity Evidence，不能证明是目标 Product Variant。",
        )
    )


class ShippingCalcOutput(StrictModel):
    destination: str
    items: list[LandedCost]
    calculation_basis: str
    estimated: bool = True


class Recommendation(LandedCost):
    reason: str
    rank: int
    constraint_evaluations: list[ConstraintEvaluation]
    score_breakdown: RankingScoreBreakdown
    offer_kind: Literal["matching_offer", "research_candidate"] = "research_candidate"


class ItemPickerOutput(StrictModel):
    mode: ResearchMode = "product_research"
    recommendations: list[Recommendation]
    matching_offers: list[LandedCost] = Field(default_factory=list)
    alternative_candidates: list[AlternativeCandidate] = Field(default_factory=list)
    unverified_candidates: list[UnverifiedCandidate] = Field(default_factory=list)
    exclusions: list[ConstraintExclusion] = Field(default_factory=list)
    working_assumptions: list[WorkingAssumption] = Field(default_factory=list)
    relaxation_suggestions: list[ConstraintRelaxationSuggestion] = Field(default_factory=list)
    match_status: Literal["matched", "no_match"] = "matched"
    rejected_count: int
    ranking_profile: RankingProfile = Field(default_factory=RankingProfile)
    preference_decisions: list[PreferenceDecision] = Field(default_factory=list)


class FileLink(StrictModel):
    file_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,180}$")
    format: ReportFormat | None = None
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    url: str
    content_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_file_metadata(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        name = payload.get("name")
        inferred = {
            ".md": ("markdown", "text/markdown; charset=utf-8"),
            ".json": ("json", "application/json; charset=utf-8"),
            ".pdf": ("pdf", "application/pdf"),
        }
        if isinstance(name, str):
            suffix = f".{name.rsplit('.', 1)[-1]}" if "." in name else ""
            format_and_type = inferred.get(suffix)
            if format_and_type is not None:
                payload.setdefault("format", format_and_type[0])
                payload.setdefault("content_type", format_and_type[1])
        return payload


class ReportNotice(StrictModel):
    code: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    message: str = Field(min_length=1, max_length=4000)


class ReportGeneratedEventData(StrictModel):
    snapshot_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    snapshot_effective_at: str = Field(min_length=1)
    files: list[FileLink] = Field(min_length=1)
    data_mode: DataMode = "live"


class ShoppingSummaryOutput(StrictModel):
    thread_id: str
    final_answer: str
    resolved_query: str | None = None
    resolved_intent: ShoppingPlan | None = None
    applied_preferences: RememberedPreference = Field(default_factory=RememberedPreference)
    task_overrides: list[TaskOverride] = Field(default_factory=list)
    constraint_relaxations: list[ConstraintRelaxation] = Field(default_factory=list)
    product_evidence: list[Candidate] = Field(default_factory=list)
    mode: ResearchMode = "product_research"
    recommendations: list[Recommendation]
    comparison: list[LandedCost]
    matching_offers: list[LandedCost] = Field(default_factory=list)
    alternative_candidates: list[AlternativeCandidate] = Field(default_factory=list)
    files: list[FileLink]
    provider_mode: Literal["live", "mixed", "sandbox"]
    providers: dict[str, ProviderMetadata] = Field(default_factory=dict)
    calculation_notice: str
    exchange_rate: ExchangeRateProvenance = Field(default_factory=ExchangeRateProvenance)
    calculation_exclusions: list[CalculationExclusion] = Field(default_factory=list)
    ranking_profile: RankingProfile = Field(default_factory=RankingProfile)
    data_mode: DataMode = "live"
    result_kind: ResultKind = "live"
    unavailable_marketplaces: list[Platform] = Field(default_factory=list)
    unverified_candidates: list[UnverifiedCandidate] = Field(default_factory=list)
    exclusions: list[ConstraintExclusion] = Field(default_factory=list)
    working_assumptions: list[WorkingAssumption] = Field(default_factory=list)
    relaxation_suggestions: list[ConstraintRelaxationSuggestion] = Field(default_factory=list)
    match_status: Literal["matched", "no_match"] = "matched"
    preference_decisions: list[PreferenceDecision] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_result_contract(self) -> ShoppingSummaryOutput:
        if not self.matching_offers:
            self.matching_offers = list(self.comparison)
        product_evidence_sources = {item.source for item in self.product_evidence}
        result_item_sources = {item.source for item in [*self.recommendations, *self.comparison]}
        provider_sources = {metadata.source for metadata in self.providers.values()}
        if self.provider_mode == "live" and any(
            source != "live" for source in product_evidence_sources
        ):
            raise ValueError("live result cannot contain non-live Product Evidence")
        if self.provider_mode == "sandbox" and any(
            source != "fixture" for source in product_evidence_sources
        ):
            raise ValueError("sandbox result cannot contain non-fixture Product Evidence")
        evidence_sources = product_evidence_sources | result_item_sources | provider_sources
        if self.provider_mode == "live" and "fixture" in evidence_sources:
            raise ValueError("live result cannot contain fixture evidence")
        if self.provider_mode == "sandbox" and any(
            source != "fixture" for source in evidence_sources
        ):
            raise ValueError("sandbox result cannot contain live evidence")
        self.data_mode = self.provider_mode
        self.unavailable_marketplaces = sorted(
            set(self.unavailable_marketplaces)
            | {
                name
                for name, metadata in self.providers.items()
                if metadata.status == "unavailable" or metadata.failure_reason is not None
            }
        )
        self.result_kind = (
            "sandbox"
            if self.provider_mode == "sandbox" and not self.unavailable_marketplaces
            else "partial"
            if self.unavailable_marketplaces or self.provider_mode == "mixed"
            else "live"
        )
        return self


class SnapshotLineage(StrictModel):
    relation: Literal["rerun", "constraint_relaxation"]
    parent_snapshot_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    parent_thread_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    parent_run_id: str = Field(pattern=r"^(?:legacy|[0-9a-f]{32})$")
    root_snapshot_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    depth: int = Field(ge=1)
    command_idempotency_key: str = Field(min_length=1, max_length=120)
    changed_constraints: list[ConstraintRelaxation] = Field(default_factory=list)


class TaskSnapshot(StrictModel):
    snapshot_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    thread_id: str
    run_id: str = Field(default="legacy", pattern=r"^(?:legacy|[0-9a-f]{32})$")
    status: Literal["running", "awaiting_clarification", "completed", "cancelled", "error"]
    query: str
    user_id: str
    data_mode: DataMode = "live"
    created_at: str
    updated_at: str
    lineage: SnapshotLineage | None = None
    resolved_query: str | None = None
    resolved_intent: ShoppingPlan | None = None
    mode: ResearchMode | None = None
    working_assumptions: list[WorkingAssumption] = Field(default_factory=list)
    applied_preferences: RememberedPreference = Field(default_factory=RememberedPreference)
    task_overrides: list[TaskOverride] = Field(default_factory=list)
    constraint_relaxations: list[ConstraintRelaxation] = Field(default_factory=list)
    provider_coverage: dict[str, ProviderMetadata] = Field(default_factory=dict)
    product_evidence: list[Candidate] = Field(default_factory=list)
    exchange_rate: ExchangeRateProvenance | None = None
    report_references: list[FileLink] = Field(default_factory=list)
    events: list[MonitorEvent] = Field(default_factory=list)
    result: ShoppingSummaryOutput | None = None
    clarification: ClarificationPrompt | None = None
    clarification_answers: dict[ClarificationField, str] = Field(default_factory=dict)
    error_code: str | None = None
    error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_snapshot_id(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("snapshot_id") is None and value.get("thread_id"):
            return {**value, "snapshot_id": value["thread_id"]}
        return value

    @model_validator(mode="after")
    def validate_result_contract(self) -> TaskSnapshot:
        evidence_sources = {item.source for item in self.product_evidence}
        if self.data_mode == "live" and any(source != "live" for source in evidence_sources):
            raise ValueError("live snapshot cannot contain non-live Product Evidence")
        if self.data_mode == "sandbox" and any(source != "fixture" for source in evidence_sources):
            raise ValueError("sandbox snapshot cannot contain non-fixture Product Evidence")
        if self.result is None:
            return self
        if self.result.data_mode != self.data_mode:
            raise ValueError("snapshot and result data modes must match")
        if self.mode is not None and self.mode != self.result.mode:
            raise ValueError("snapshot and result research modes must match")
        if (
            self.resolved_intent is not None
            and self.result.resolved_intent is not None
            and self.resolved_intent != self.result.resolved_intent
        ):
            raise ValueError("snapshot and result resolved intents must match")
        if self.product_evidence and self.product_evidence != self.result.product_evidence:
            raise ValueError("snapshot and result Product Evidence must match")
        if self.provider_coverage and self.provider_coverage != self.result.providers:
            raise ValueError("snapshot and result provider coverage must match")
        if self.report_references and self.report_references != self.result.files:
            raise ValueError("snapshot and result report references must match")
        return self


class ResearchReportSnapshot(ShoppingSummaryOutput):
    """Immutable report projection built from one completed Research Snapshot."""

    report_schema_version: Literal["1"] = "1"
    snapshot_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    snapshot_effective_at: str = Field(min_length=1)
    snapshot_created_at: str = Field(min_length=1)
    snapshot_status: Literal["completed"] = "completed"
    user_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    query: str = Field(min_length=1, max_length=4000)
    lineage: SnapshotLineage | None = None
    notices: list[ReportNotice] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_snapshot_projection(self) -> ResearchReportSnapshot:
        if self.snapshot_id != self.thread_id:
            raise ValueError("report snapshot and result thread IDs must match")
        if self.resolved_intent is not None and self.resolved_intent.mode != self.mode:
            raise ValueError("report snapshot and resolved intent modes must match")
        return self


class ReportListResponse(StrictModel):
    status: Literal["ready"] = "ready"
    snapshot_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    snapshot_effective_at: str = Field(min_length=1)
    files: list[FileLink] = Field(min_length=1)


class ReportGenerationResponse(ReportListResponse):
    idempotent: bool = True


class TaskSnapshotMessage(StrictModel):
    type: Literal["task_snapshot"] = "task_snapshot"
    snapshot: TaskSnapshot
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


class TaskRerunResponse(StrictModel):
    status: Literal["started"] = "started"
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    parent_snapshot_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    lineage: SnapshotLineage
    idempotent: bool = False


class ResearchHistoryResponse(StrictModel):
    snapshots: list[TaskSnapshot] = Field(default_factory=list)


class UploadResponse(StrictModel):
    upload_id: str
    name: str
    content_type: str
    size: int


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    service: Literal["shopping-agent"] = "shopping-agent"
    version: str


class ProviderCapability(StrictModel):
    configured: bool
    state: Literal["configured", "partial", "missing"]
    available: bool = False
    source: Literal["live", "fixture"] = "live"
    failure_reason: ProviderFailureReason | None = None


class ReadinessResponse(StrictModel):
    status: Literal["ready", "degraded", "not_ready"]
    task_ready: bool
    environment: Literal["development", "test", "production"]
    runtime_mode: Literal["live", "sandbox"]
    agent_mode: Literal["llm", "rules", "unavailable"]
    requested_agent_mode: Literal["auto", "llm", "rules"]
    preference_store: Literal["memory", "redis"]
    providers: dict[str, ProviderCapability]
    capabilities: dict[str, bool]
    required_actions: list[str]
    data_mode: DataMode = "live"
    developer_diagnostic_mode: bool = False
    preference_backend: PreferenceBackendStatus = Field(
        default_factory=lambda: PreferenceBackendStatus(
            requested_backend="memory",
            backend="memory",
            durability="local_evaluation",
        )
    )
