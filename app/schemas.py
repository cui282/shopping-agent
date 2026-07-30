from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["amazon", "shopee", "aliexpress", "ebay"]
ProviderSource = Literal["live", "curated", "fixture", "computed"]
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


class TaskStarted(StrictModel):
    status: Literal["started"] = "started"
    thread_id: str


class MonitorEvent(StrictModel):
    type: Literal["monitor_event"] = "monitor_event"
    event: EventName
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


class ProviderMetadata(StrictModel):
    source: ProviderSource
    provider: str
    status: Literal["ok", "degraded", "unavailable"] = "ok"
    fallback_reason: str | None = None


class ShoppingPlan(StrictModel):
    budget_cny: float | None = None
    category: str
    material_preferences: list[str] = Field(default_factory=list)
    style_preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    destination: str = "中国大陆"
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


class PriceCompareOutput(StrictModel):
    base_currency: Literal["CNY"] = "CNY"
    ranked: list[PricePoint]
    cheapest_per_platform: dict[str, PricePoint]
    rate_source: str
    rates_as_of: str
    excluded_currencies: list[str] = Field(default_factory=list)


class LandedCost(PricePoint):
    shipping_cny: float
    duty_cny: float
    landed_cny: float
    eta_days: int
    duty_tier: Literal["免征", "标准", "高税"]


class ShippingCalcOutput(StrictModel):
    destination: str
    items: list[LandedCost]
    calculation_basis: str
    estimated: bool = True


class Recommendation(LandedCost):
    reason: str
    rank: int


class ItemPickerOutput(StrictModel):
    recommendations: list[Recommendation]
    rejected_count: int


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


class TaskSnapshot(StrictModel):
    thread_id: str
    status: Literal["running", "completed", "cancelled", "error"]
    query: str
    user_id: str
    created_at: str
    updated_at: str
    result: ShoppingSummaryOutput | None = None
    error_code: str | None = None
    error: str | None = None


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
