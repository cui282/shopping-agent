from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypeAliasType

Platform = Literal["amazon", "shopee", "aliexpress", "ebay"]
ProviderSource = Literal["live", "curated", "fixture", "computed"]
CurrencyConversionPurpose = Literal["comparison_estimate"]
CurrencyConversionRateType = Literal[
    "provider_quote",
    "card_network_estimate",
    "mid_market_reference",
    "sandbox_fixture",
]
CurrencyMarkupStatus = Literal["included", "excluded", "unknown"]
ShippingQuoteType = Literal[
    "carrier_quote",
    "marketplace_checkout",
    "shipping_included",
    "sandbox_fixture",
]
CustomsFxRateBasis = Literal[
    "monthly_customs_assessment",
    "customs_prescribed_adjustment",
]
DataMode = Literal["live", "sandbox", "mixed"]
ResearchMode = Literal["product_research", "exact_offer_comparison"]
ContextMessageRole = Literal["system", "user", "assistant", "tool"]
RecallChannelName = Literal["opensearch", "query_tower", "item_tower", "faiss"]
RecallChannelState = Literal["configured", "ready", "degraded", "unavailable"]
ReadinessComponentState = Literal["ready", "configured", "degraded", "unavailable", "disabled"]
RecallMode = Literal["hybrid", "partial_hybrid", "deterministic_fallback"]
PersonalizationInputSource = Literal["remembered_preference", "none"]
PersonalizationSignal = Literal["user_tower", "none"]
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
    "circuit_open",
]
OfferLinkKind = Literal["product_detail", "marketplace_search"]
ImportRegime = Literal[
    "general_trade",
    "cross_border_ecommerce",
    "personal_postal",
    "seller_collected",
]
TaxRateType = Literal[
    "mfn",
    "agreement",
    "preferential",
    "temporary",
    "ordinary",
    "cross_border_policy",
    "personal_postal",
    "provider_quote",
]
TaxCalculationMethod = Literal[
    "statutory_formula",
    "cross_border_policy",
    "personal_postal_rate",
    "provider_quote",
]
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
CalculationExclusionReason = Literal[
    "unsupported_currency",
    "missing_fx_evidence",
    "invalid_fx_evidence",
    "invalid_amount",
]
ClarificationField = Literal["mode", "product_variant", "destination"]
ClarificationReasonCode = Literal[
    "mode_ambiguous",
    "product_variant_ambiguous",
    "destination_ambiguous",
]
EventName = Literal[
    "session_created",
    "queue_status",
    "intent_resolved",
    "assistant_call",
    "context_compression",
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


class TaskDeleteCommand(StrictModel):
    user_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")


class TaskDeleteResponse(StrictModel):
    status: Literal["deleted"] = "deleted"
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class TaskTombstone(StrictModel):
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    user_id: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    tenant_id: str = Field(default="default", max_length=160, pattern=r"^[A-Za-z0-9_.:@-]+$")
    generation: int = Field(ge=1)
    deleted_at: str = Field(min_length=1)


class ReferenceImageBinding(StrictModel):
    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(pattern=r"^[0-9a-f]{32}\.(?:jpg|png|webp)$")
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size: int = Field(ge=0)
    ownership: Literal["task_owned_copy"] = "task_owned_copy"
    bound_at: str = Field(min_length=1)


class LegacyReferenceImageBinding(StrictModel):
    """Read-only compatibility for snapshots written before task binding existed."""

    upload_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    name: str = Field(pattern=r"^[0-9a-f]{32}\.(?:jpg|png|webp)$")
    content_type: str = Field(min_length=1)
    size: int = Field(ge=0)


class ProviderMetadata(StrictModel):
    source: ProviderSource
    provider: str
    status: Literal["ok", "degraded", "unavailable"] = "ok"
    fallback_reason: str | None = None
    failure_reason: ProviderFailureReason | None = None


class RecallChannelReport(StrictModel):
    channel: RecallChannelName
    configured: bool = False
    state: RecallChannelState
    reason_code: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    reason: str = Field(min_length=1, max_length=4000)
    participated: bool = False


class PersonalizationReport(StrictModel):
    """Trace the optional user-tower input without exposing a raw embedding."""

    configured: bool = False
    state: RecallChannelState
    input_source: PersonalizationInputSource = "none"
    preference_fields: list[PreferenceField] = Field(default_factory=list)
    preference_values: list[str] = Field(default_factory=list)
    signal: PersonalizationSignal = "none"
    dimension: int | None = Field(default=None, ge=1)
    matched_candidate_count: int = Field(default=0, ge=0)
    reason_code: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    reason: str = Field(min_length=1, max_length=4000)
    participated: bool = False


class RecallProvenance(StrictModel):
    mode: RecallMode
    channels: dict[RecallChannelName, RecallChannelReport] = Field(default_factory=dict)
    participating_channels: list[RecallChannelName] = Field(default_factory=list)
    fallback_reason: str | None = None
    input_candidate_count: int = Field(ge=0)
    selected_candidate_count: int = Field(ge=0)
    personalization: PersonalizationReport | None = None


class RecallReadiness(StrictModel):
    mode: RecallMode
    channels: dict[RecallChannelName, RecallChannelReport] = Field(default_factory=dict)
    required_actions: list[str] = Field(default_factory=list)
    personalization: PersonalizationReport | None = None


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
    explanation: str = "不同商品推荐不要求跨平台同款证明。"


class OfferProvenance(StrictModel):
    kind: Literal["marketplace_gateway", "sandbox_fixture"]
    provider: str | None = None
    upstream_source: str | None = None


class CurrencyConversionEvidence(StrictModel):
    """A time-bound provider quote used only to normalize an offer for comparison."""

    source_currency: str = Field(pattern=r"^[A-Z]{3}$")
    target_currency: Literal["CNY"] = "CNY"
    rate_to_cny: float = Field(gt=0)
    purpose: CurrencyConversionPurpose = "comparison_estimate"
    rate_type: CurrencyConversionRateType
    markup_status: CurrencyMarkupStatus = "unknown"
    markup_bps: float | None = Field(default=None, ge=0, le=10_000)
    provider: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=500)
    observed_at: str = Field(min_length=1)
    expires_at: str | None = Field(default=None, min_length=1)

    @field_validator("source_currency", mode="before")
    @classmethod
    def normalize_source_currency(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("observed_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("currency conversion timestamps must be valid ISO datetimes") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("currency conversion timestamps must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def validate_quote(self) -> CurrencyConversionEvidence:
        if self.source_currency == self.target_currency:
            raise ValueError("currency conversion evidence is only valid across currencies")
        if self.markup_bps is not None and self.markup_status != "included":
            raise ValueError("markup_bps requires markup_status=included")
        if self.expires_at is not None:
            observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if expires <= observed:
                raise ValueError("currency conversion expires_at must be after observed_at")
        return self


class ShippingQuoteEvidence(StrictModel):
    """A route- and service-specific shipping quote supplied by the data channel."""

    quote_type: ShippingQuoteType
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    total_amount: float = Field(ge=0)
    base_amount: float | None = Field(default=None, ge=0)
    surcharge_amount: float = Field(default=0, ge=0)
    discount_amount: float = Field(default=0, ge=0)
    actual_weight_kg: float | None = Field(default=None, gt=0)
    dimensional_weight_kg: float | None = Field(default=None, gt=0)
    chargeable_weight_kg: float | None = Field(default=None, gt=0)
    length_cm: float | None = Field(default=None, gt=0)
    width_cm: float | None = Field(default=None, gt=0)
    height_cm: float | None = Field(default=None, gt=0)
    dimensional_divisor: float | None = Field(default=None, gt=0)
    origin_country: str = Field(min_length=2, max_length=80)
    destination_country: str = Field(default="CN", min_length=2, max_length=80)
    service_name: str = Field(min_length=1, max_length=200)
    eta_min_days: int = Field(ge=0)
    eta_max_days: int = Field(ge=0)
    provider: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=500)
    observed_at: str = Field(min_length=1)
    expires_at: str | None = Field(default=None, min_length=1)
    currency_conversion: CurrencyConversionEvidence | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("origin_country", "destination_country", mode="before")
    @classmethod
    def normalize_shipping_country(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized.upper() if len(normalized) == 2 else normalized
        return value

    @field_validator("observed_at", "expires_at")
    @classmethod
    def validate_shipping_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("shipping quote timestamps must be valid ISO datetimes") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("shipping quote timestamps must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def validate_shipping_quote(self) -> ShippingQuoteEvidence:
        if self.destination_country != "CN":
            raise ValueError("shipping quote must target destination_country=CN")
        if self.eta_max_days < self.eta_min_days:
            raise ValueError("eta_max_days must be greater than or equal to eta_min_days")
        if (
            self.base_amount is not None
            and abs(
                self.total_amount
                - (self.base_amount + self.surcharge_amount - self.discount_amount)
            )
            > 0.01
        ):
            raise ValueError("shipping total must reconcile with base, surcharge, and discount")
        known_weights = [
            value
            for value in (self.actual_weight_kg, self.dimensional_weight_kg)
            if value is not None
        ]
        if (
            self.chargeable_weight_kg is not None
            and known_weights
            and self.chargeable_weight_kg < max(known_weights)
        ):
            raise ValueError("chargeable weight cannot be lower than known package weights")
        dimensions = (self.length_cm, self.width_cm, self.height_cm)
        if any(value is not None for value in dimensions) and not all(
            value is not None for value in dimensions
        ):
            raise ValueError("package dimensions must include length, width, and height")
        if self.dimensional_divisor is not None and not all(
            value is not None for value in dimensions
        ):
            raise ValueError("dimensional divisor requires complete package dimensions")
        if self.currency == "CNY" and self.currency_conversion is not None:
            raise ValueError("CNY shipping quotes do not require currency conversion")
        if self.currency != "CNY":
            if self.currency_conversion is None:
                raise ValueError("non-CNY shipping quote requires currency conversion evidence")
            if self.currency_conversion.source_currency != self.currency:
                raise ValueError("shipping currency conversion must match quote currency")
        if self.expires_at is not None:
            observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if expires <= observed:
                raise ValueError("shipping quote expires_at must be after observed_at")
        return self


class CustomsExchangeRateEvidence(StrictModel):
    """China Customs assessment rate for the declaration month, separate from payment FX."""

    source_currency: str = Field(pattern=r"^[A-Z]{3}$")
    target_currency: Literal["CNY"] = "CNY"
    rate_to_cny: float = Field(gt=0)
    rate_basis: CustomsFxRateBasis = "monthly_customs_assessment"
    declaration_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    assessment_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    provider: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=500)

    @field_validator("source_currency", mode="before")
    @classmethod
    def normalize_customs_source_currency(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_customs_rate_period(self) -> CustomsExchangeRateEvidence:
        try:
            declaration = date.fromisoformat(self.declaration_date)
            date.fromisoformat(f"{self.assessment_month}-01")
        except ValueError as exc:
            raise ValueError("customs exchange-rate dates must be valid calendar dates") from exc
        if declaration.strftime("%Y-%m") != self.assessment_month:
            raise ValueError("customs assessment_month must match the declaration month")
        if self.source_currency == self.target_currency:
            raise ValueError("customs exchange evidence is only valid across currencies")
        return self


class CustomsValuationEvidence(StrictModel):
    """Provider-supplied CIF customs value and its independently auditable components."""

    valuation_method: Literal["transaction_value_cif"] = "transaction_value_cif"
    goods_value_original: float = Field(ge=0)
    goods_currency: str = Field(pattern=r"^[A-Z]{3}$")
    goods_value_cny: float = Field(ge=0)
    international_shipping_cny: float = Field(ge=0)
    insurance_cny: float = Field(default=0, ge=0)
    customs_value_cny: float = Field(ge=0)
    customs_conversion: CustomsExchangeRateEvidence | None = None
    provider: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=500)

    @field_validator("goods_currency", mode="before")
    @classmethod
    def normalize_goods_currency(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def reconcile_customs_value(self) -> CustomsValuationEvidence:
        cent = Decimal("0.01")
        if self.goods_currency == "CNY":
            if self.customs_conversion is not None:
                raise ValueError("CNY customs goods value does not require exchange evidence")
            expected_goods = Decimal(str(self.goods_value_original)).quantize(
                cent, rounding=ROUND_HALF_UP
            )
        else:
            if self.customs_conversion is None:
                raise ValueError("non-CNY customs value requires customs exchange evidence")
            if self.customs_conversion.source_currency != self.goods_currency:
                raise ValueError("customs exchange currency must match goods_currency")
            expected_goods = (
                Decimal(str(self.goods_value_original))
                * Decimal(str(self.customs_conversion.rate_to_cny))
            ).quantize(cent, rounding=ROUND_HALF_UP)
        if abs(expected_goods - Decimal(str(self.goods_value_cny))) > cent:
            raise ValueError("goods_value_cny must reconcile with the customs exchange rate")
        expected_customs = (
            Decimal(str(self.goods_value_cny))
            + Decimal(str(self.international_shipping_cny))
            + Decimal(str(self.insurance_cny))
        ).quantize(cent, rounding=ROUND_HALF_UP)
        if abs(expected_customs - Decimal(str(self.customs_value_cny))) > cent:
            raise ValueError("customs_value_cny must reconcile with CIF components")
        return self


class CustomsTaxEvidence(StrictModel):
    """Provider-supplied classification and rate snapshot for imports into China."""

    hs_code: str = Field(pattern=r"^\d{6,10}$")
    country_of_origin: str = Field(min_length=2, max_length=80)
    destination_country: str = Field(default="CN", min_length=2, max_length=80)
    ship_from_country: str | None = Field(default=None, min_length=2, max_length=80)
    import_regime: ImportRegime
    rate_type: TaxRateType
    tariff_rate: float | None = Field(default=None, ge=0, le=5)
    import_vat_rate: float | None = Field(default=None, ge=0, le=1)
    consumption_tax_rate: float = Field(default=0, ge=0, lt=1)
    personal_postal_tax_rate: float | None = Field(default=None, ge=0, le=5)
    personal_postal_assessed_value_cny: float | None = Field(default=None, ge=0)
    personal_postal_total_value_cny: float | None = Field(default=None, ge=0)
    personal_postal_value_limit_cny: float | None = Field(default=None, gt=0)
    personal_postal_tax_exemption_threshold_cny: float | None = Field(default=None, ge=0)
    personal_postal_single_indivisible_item: bool | None = None
    personal_postal_eligible: bool | None = None
    seller_collected_tax_cny: float | None = Field(default=None, ge=0)
    insurance_cny: float = Field(default=0, ge=0)
    valuation: CustomsValuationEvidence | None = None
    cross_border_ecommerce_eligible: bool | None = None
    provider: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=500)
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("country_of_origin", "destination_country", "ship_from_country", mode="before")
    @classmethod
    def normalize_country(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized.upper() if len(normalized) == 2 else normalized
        return value

    @field_validator("effective_date")
    @classmethod
    def validate_effective_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("effective_date must be a valid ISO calendar date") from exc
        return value

    @model_validator(mode="after")
    def validate_regime_evidence(self) -> CustomsTaxEvidence:
        if self.destination_country != "CN":
            raise ValueError("customs tax evidence must target destination_country=CN")
        if (
            self.valuation is not None
            and abs(self.insurance_cny - self.valuation.insurance_cny) > 0.01
        ):
            raise ValueError("customs insurance_cny must match valuation insurance_cny")
        if self.import_regime == "general_trade":
            if self.rate_type in {
                "cross_border_policy",
                "personal_postal",
                "provider_quote",
            }:
                raise ValueError("general_trade requires a statutory tariff rate type")
            if self.tariff_rate is None or self.import_vat_rate is None:
                raise ValueError("general_trade requires tariff_rate and import_vat_rate")
        elif self.import_regime == "cross_border_ecommerce":
            if self.rate_type != "cross_border_policy":
                raise ValueError("cross_border_ecommerce requires cross_border_policy rate type")
            if self.cross_border_ecommerce_eligible is not True:
                raise ValueError("cross_border_ecommerce requires explicit policy eligibility")
            if self.import_vat_rate is None:
                raise ValueError("cross_border_ecommerce requires import_vat_rate")
            if self.tariff_rate not in {None, 0}:
                raise ValueError("eligible cross_border_ecommerce tariff_rate must be zero")
        elif self.import_regime == "personal_postal":
            if self.rate_type != "personal_postal" or self.personal_postal_tax_rate is None:
                raise ValueError("personal_postal requires its comprehensive tax rate")
            if self.personal_postal_eligible is not True:
                raise ValueError("personal_postal requires explicit personal-use eligibility")
            if (
                self.personal_postal_assessed_value_cny is None
                or self.personal_postal_total_value_cny is None
                or self.personal_postal_value_limit_cny is None
                or self.personal_postal_tax_exemption_threshold_cny is None
                or self.personal_postal_single_indivisible_item is None
            ):
                raise ValueError(
                    "personal_postal requires assessed value, total value, policy limits, "
                    "and parcel divisibility evidence"
                )
            if (
                self.personal_postal_total_value_cny > self.personal_postal_value_limit_cny
                and self.personal_postal_single_indivisible_item is not True
            ):
                raise ValueError(
                    "personal_postal above the value limit requires a single indivisible item"
                )
        elif self.import_regime == "seller_collected":
            if self.rate_type != "provider_quote" or self.seller_collected_tax_cny is None:
                raise ValueError("seller_collected requires a provider tax quote")
        return self


class MarketplaceDemand(StrictModel):
    platform: Platform
    query: str = Field(min_length=1, max_length=4000)


AgentStep = Literal[
    "planner",
    "category_insight",
    "item_search",
    "recall",
    "price_compare",
    "shipping_calc",
    "item_picker",
    "shopping_summary",
]


class TaskToolCommand(StrictModel):
    """A model request to schedule research work; it never contains Product Evidence."""

    platforms: list[Platform] = Field(default_factory=list, max_length=4)
    parallel: bool = True
    steps: list[AgentStep] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=1000)


class AgentExecutionPlan(StrictModel):
    """Validated, bounded execution intent produced by the model boundary."""

    platforms: list[Platform] = Field(default_factory=list, max_length=4)
    fork: bool = False
    steps: list[AgentStep] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=1000)
    source: Literal["model", "rules"] = "rules"


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
    reference_images: list[ReferenceImageBinding | LegacyReferenceImageBinding]
    data_mode: DataMode = "live"


class ToolStartEventData(StrictModel):
    tool_name: str = Field(min_length=1)
    args: dict[str, Any]
    data_mode: DataMode = "live"


class AssistantCallEventData(StrictModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    step: str = Field(min_length=1)
    data_mode: DataMode = "live"


class ContextCompressionEventData(StrictModel):
    status: Literal["applied", "degraded", "not_needed"]
    reason_code: str = Field(min_length=1, pattern=r"^[a-z0-9_:-]+$")
    compressed_message_count: int = Field(ge=0)
    retained_message_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    summary_fields: list[str] = Field(default_factory=list, max_length=32)
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
    recall_provenance: RecallProvenance | None = None

    @model_validator(mode="after")
    def outcome_matches_status(self) -> ToolEndEventData:
        expected = {"success": "ok", "degraded": "degraded", "failure": "unavailable"}
        if self.status != expected[self.outcome]:
            raise ValueError("tool outcome must match provider status")
        return self


class TaskCancelledEventData(StrictModel):
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    data_mode: DataMode = "live"


class QueueStatusEventData(StrictModel):
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    queue_type: Literal["normal", "heavy"]
    position: int = Field(ge=0)
    estimated_wait_seconds: float = Field(ge=0)
    dialog_turns: int = Field(ge=0)
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
            "queue_status": QueueStatusEventData,
            "intent_resolved": IntentResolvedEventData,
            "assistant_call": AssistantCallEventData,
            "context_compression": ContextCompressionEventData,
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
    providers: list[str] = Field(default_factory=list)
    quote_count: int = Field(default=0, ge=0)
    settlement_notice: str = Field(
        default=(
            "人民币金额仅用于研究时点比价；最终支付金额以结算页、发卡行或支付机构的"
            "实际汇率、费用和动态货币转换选择为准。"
        ),
        min_length=1,
    )


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


class UserTowerInput(StrictModel):
    """Only explicit, persisted preferences may cross the user-tower boundary."""

    anonymous_shopper_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    remembered_preference: RememberedPreference = Field(default_factory=RememberedPreference)


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


class ContextClarificationResponse(StrictModel):
    field: ClarificationField
    reason_code: ClarificationReasonCode
    response: str = Field(min_length=1, max_length=4000)
    resolved_value: str | None = Field(default=None, min_length=1, max_length=4000)


class ContextPreferenceSource(StrictModel):
    field: PreferenceField
    value: str = Field(min_length=1)
    source: Literal["remembered_preference", "task_override"]


class ContextSummary(StrictModel):
    """Typed, derived facts that may be sent to a model but never become authority."""

    mode: ResearchMode | None = None
    category: str | None = None
    destination: str | None = None
    supported_destination: str | None = None
    resolved_hard_constraints: list[HardConstraint] = Field(default_factory=list)
    product_variant: str | None = None
    exact_identity: str | None = None
    clarification_responses: list[ContextClarificationResponse] = Field(default_factory=list)
    working_assumptions: list[WorkingAssumption] = Field(default_factory=list)
    remembered_preference: RememberedPreference = Field(default_factory=RememberedPreference)
    task_overrides: list[TaskOverride] = Field(default_factory=list)
    preference_sources: list[ContextPreferenceSource] = Field(default_factory=list)
    pending_clarification: ClarificationPrompt | None = None


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


class CategoryEvidence(StrictModel):
    document_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=4000)
    score: float = Field(ge=0)


class CategoryInsightOutput(StrictModel):
    category: str
    components: list[str]
    bestsellers: list[Bestseller]
    attributes: list[AttributeDist]
    price_tiers: list[PriceTier]
    confidence: float = Field(ge=0, le=1)
    evidence: list[CategoryEvidence] = Field(default_factory=list)
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
    price_conversion: CurrencyConversionEvidence | None = None
    shipping_quote: ShippingQuoteEvidence | None = None
    customs: CustomsTaxEvidence | None = None

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


class RecallResult(StrictModel):
    candidates: list[Candidate]
    total_recall: int = Field(ge=0)
    truncated: bool = False
    provenance: RecallProvenance
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
    price_conversion: CurrencyConversionEvidence | None = None
    shipping_quote: ShippingQuoteEvidence | None = None
    customs: CustomsTaxEvidence | None = None

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


class ImportTaxBreakdown(StrictModel):
    import_regime: ImportRegime
    calculation_method: TaxCalculationMethod
    hs_code: str
    country_of_origin: str
    destination_country: str
    customs_value_cny: float = Field(ge=0)
    customs_valuation: CustomsValuationEvidence | None = None
    rate_type: TaxRateType
    tariff_rate: float | None = Field(default=None, ge=0, le=5)
    import_vat_rate: float | None = Field(default=None, ge=0, le=1)
    consumption_tax_rate: float | None = Field(default=None, ge=0, lt=1)
    personal_postal_tax_rate: float | None = Field(default=None, ge=0, le=5)
    personal_postal_assessed_value_cny: float | None = Field(default=None, ge=0)
    personal_postal_total_value_cny: float | None = Field(default=None, ge=0)
    personal_postal_value_limit_cny: float | None = Field(default=None, gt=0)
    personal_postal_tax_exemption_threshold_cny: float | None = Field(default=None, ge=0)
    personal_postal_single_indivisible_item: bool | None = None
    policy_factor: float = Field(default=1, ge=0, le=1)
    tariff_cny: float | None = Field(default=None, ge=0)
    import_vat_cny: float | None = Field(default=None, ge=0)
    consumption_tax_cny: float | None = Field(default=None, ge=0)
    tax_before_exemption_cny: float | None = Field(default=None, ge=0)
    tax_exemption_cny: float = Field(default=0, ge=0)
    tax_exemption_reason: str | None = None
    total_import_tax_cny: float = Field(ge=0)
    provider: str
    source_reference: str
    effective_date: str
    calculation_basis: str


class TaxCalculationExclusion(StrictModel):
    item_id: str
    platform: Platform
    title: str
    reason_code: Literal[
        "missing_customs_evidence",
        "missing_customs_valuation",
        "invalid_customs_evidence",
        "unsupported_tax_destination",
    ]
    reason: str


class ShippingCalculationExclusion(StrictModel):
    item_id: str
    platform: Platform
    title: str
    reason_code: Literal[
        "missing_shipping_quote",
        "invalid_shipping_quote",
        "expired_shipping_quote",
    ]
    reason: str


class LandedCost(PricePoint):
    shipping_cny: float
    insurance_cny: float = 0
    duty_cny: float | None
    import_vat_cny: float | None = None
    consumption_tax_cny: float | None = None
    import_tax_cny: float | None = None
    landed_cny: float
    eta_days: int
    duty_tier: Literal["免征", "标准", "高税"]
    shipping_estimate: EstimateDisclosure = Field(default_factory=EstimateDisclosure)
    duty_estimate: EstimateDisclosure = Field(default_factory=EstimateDisclosure)
    tax_estimate: EstimateDisclosure = Field(default_factory=EstimateDisclosure)
    delivery_estimate: EstimateDisclosure = Field(default_factory=EstimateDisclosure)
    tax_breakdown: ImportTaxBreakdown | None = None


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
    insurance_cny: float | None = None
    duty_cny: float | None = None
    import_vat_cny: float | None = None
    consumption_tax_cny: float | None = None
    import_tax_cny: float | None = None
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
    price_conversion: CurrencyConversionEvidence | None = None
    shipping_quote: ShippingQuoteEvidence | None = None
    customs: CustomsTaxEvidence | None = None
    tax_breakdown: ImportTaxBreakdown | None = None


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
    shipping_exclusions: list[ShippingCalculationExclusion] = Field(default_factory=list)
    tax_exclusions: list[TaxCalculationExclusion] = Field(default_factory=list)


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
    shipping_exclusions: list[ShippingCalculationExclusion] = Field(default_factory=list)
    tax_exclusions: list[TaxCalculationExclusion] = Field(default_factory=list)
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
    recall_provenance: RecallProvenance | None = None

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
    generation: int = Field(default=0, ge=0)
    status: Literal["running", "awaiting_clarification", "completed", "cancelled", "error"]
    query: str
    user_id: str
    tenant_id: str = Field(default="default", max_length=160, pattern=r"^[A-Za-z0-9_.:@-]+$")
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
    recall_provenance: RecallProvenance | None = None

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
        if (
            self.recall_provenance is not None
            and self.result.recall_provenance is not None
            and self.recall_provenance != self.result.recall_provenance
        ):
            raise ValueError("snapshot and result recall provenance must match")
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


class ReadinessComponentStatus(StrictModel):
    """A conservative runtime/configuration observation used by readiness clients."""

    configured: bool = False
    ready: bool = False
    state: ReadinessComponentState
    reason_code: str = Field(min_length=1, pattern=r"^[a-z0-9_.:-]+$")
    reason: str = Field(min_length=1, max_length=4000)


class ReadinessComponents(StrictModel):
    llm: ReadinessComponentStatus
    marketplace_gateways: dict[str, ReadinessComponentStatus] = Field(default_factory=dict)
    redis: ReadinessComponentStatus
    opensearch: ReadinessComponentStatus
    faiss: ReadinessComponentStatus
    query_tower: ReadinessComponentStatus
    item_tower: ReadinessComponentStatus
    user_tower: ReadinessComponentStatus
    storage: ReadinessComponentStatus
    image_analysis: ReadinessComponentStatus


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
    components: ReadinessComponents
    preference_backend: PreferenceBackendStatus = Field(
        default_factory=lambda: PreferenceBackendStatus(
            requested_backend="memory",
            backend="memory",
            durability="local_evaluation",
        )
    )
    recall: RecallReadiness = Field(
        default_factory=lambda: RecallReadiness(mode="deterministic_fallback")
    )
    release_channel: Literal["stable", "canary"] = "stable"
    release_id: str = "local"
    draining: bool = False
    rollback: bool = False
