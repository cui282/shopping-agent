from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Platform = Literal["amazon", "shopee", "aliexpress", "ebay"]
ProviderSource = Literal["live", "curated", "fixture", "computed"]
DataMode = Literal["live", "sandbox", "mixed"]
ResultKind = Literal["live", "sandbox", "partial"]
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
CalculationExclusionReason = Literal["unsupported_currency", "invalid_amount"]
EventName = Literal[
    "session_created",
    "assistant_call",
    "tool_start",
    "tool_end",
    "fork",
    "task_result",
    "task_cancelled",
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
            "assistant_call": AssistantCallEventData,
            "tool_start": ToolStartEventData,
            "tool_end": ToolEndEventData,
            "fork": ForkEventData,
            "task_cancelled": TaskCancelledEventData,
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


class ShoppingPlan(StrictModel):
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
    attributes: dict[str, Any] = Field(default_factory=dict)
    source: ProviderSource
    marketplace: Platform | None = None
    offer_id: str | None = None
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    variant_attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    availability: str | None = None
    retrieved_at: str | None = None
    provenance: OfferProvenance | None = None
    link_kind: OfferLinkKind | None = None

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
    attributes: dict[str, Any] = Field(default_factory=dict)
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


class ItemPickerOutput(StrictModel):
    recommendations: list[Recommendation]
    unverified_candidates: list[UnverifiedCandidate] = Field(default_factory=list)
    exclusions: list[ConstraintExclusion] = Field(default_factory=list)
    working_assumptions: list[WorkingAssumption] = Field(default_factory=list)
    relaxation_suggestions: list[ConstraintRelaxationSuggestion] = Field(default_factory=list)
    match_status: Literal["matched", "no_match"] = "matched"
    rejected_count: int
    ranking_profile: RankingProfile = Field(default_factory=RankingProfile)


class FileLink(StrictModel):
    name: str
    url: str


class ShoppingSummaryOutput(StrictModel):
    thread_id: str
    final_answer: str
    recommendations: list[Recommendation]
    comparison: list[LandedCost]
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

    @model_validator(mode="after")
    def normalize_result_contract(self) -> ShoppingSummaryOutput:
        evidence_sources = {item.source for item in [*self.recommendations, *self.comparison]} | {
            metadata.source for metadata in self.providers.values()
        }
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


class TaskSnapshot(StrictModel):
    thread_id: str
    run_id: str = Field(default="legacy", pattern=r"^(?:legacy|[0-9a-f]{32})$")
    status: Literal["running", "completed", "cancelled", "error"]
    query: str
    user_id: str
    data_mode: DataMode = "live"
    created_at: str
    updated_at: str
    events: list[MonitorEvent] = Field(default_factory=list)
    result: ShoppingSummaryOutput | None = None
    error_code: str | None = None
    error: str | None = None


class TaskSnapshotMessage(StrictModel):
    type: Literal["task_snapshot"] = "task_snapshot"
    snapshot: TaskSnapshot
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


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
