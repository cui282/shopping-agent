export type Marketplace = "amazon" | "shopee" | "aliexpress" | "ebay" | string;

export type ProviderSource = "live" | "curated" | "fixture" | "computed";
export type DataMode = "live" | "sandbox" | "mixed";
export type ProviderMode = DataMode | "unverified";
export type ResultKind = "live" | "sandbox" | "partial";
export type ProviderFailureReason =
  | "not_configured"
  | "request_failed"
  | "empty_response"
  | "sandbox_forbidden";
export type OfferLinkKind = "product_detail" | "marketplace_search";
export type ConstraintStatus = "satisfied" | "violated" | "unknown";
export type ConstraintKind = "budget" | "material" | "attribute" | "specification";
export type ConstraintOperator =
  | "lte"
  | "gte"
  | "equals"
  | "not_equals"
  | "contains"
  | "not_contains";
export type RankingDimension = "landed_cost" | "preference_match" | "evidence_quality" | "delivery_time";

export interface HardConstraint {
  id: string;
  kind: ConstraintKind;
  field: string;
  operator: ConstraintOperator;
  value: string | number;
  unit: string | null;
  label: string;
}

export interface WorkingAssumption {
  code: string;
  field: string;
  value: string;
  reason: string;
}

export interface RankingProfile {
  priority_order: RankingDimension[];
  explicit: boolean;
}

export interface RankingScoreBreakdown {
  priority_order: RankingDimension[];
  landed_cost_cny: number;
  landed_cost_score: number;
  preference_match_score: number;
  evidence_quality_score: number;
  delivery_time_days: number;
  delivery_time_score: number;
}

export interface ExchangeRateProvenance {
  base_currency: "CNY";
  source: string;
  effective_date: string;
  calculation_basis: string;
}

export interface CalculationExclusion {
  item_id: string;
  platform: Marketplace;
  title: string;
  currency: string;
  amount: number | null;
  reason_code: "unsupported_currency" | "invalid_amount";
  reason: string;
}

export interface EstimateDisclosure {
  estimated: boolean;
  source: string;
  calculation_basis: string;
}

export interface ConstraintEvidence {
  field_path: string;
  value: string | number | boolean | null;
  source: "product_evidence" | "computed";
}

export interface ConstraintEvaluation {
  constraint: HardConstraint;
  status: ConstraintStatus;
  reason_code: string;
  explanation: string;
  evidence: ConstraintEvidence[];
}

export interface ProductIdentity {
  gtin: string | null;
  mpn: string | null;
  brand: string | null;
  model: string | null;
}

export interface OfferProvenance {
  kind: "marketplace_gateway" | "sandbox_fixture";
  provider: string | null;
  upstream_source: string | null;
}

export interface ProductEvidence {
  item_id: string;
  platform: Marketplace;
  marketplace: Marketplace;
  offer_id: string | null;
  title: string;
  product_url: string | null;
  link_kind: OfferLinkKind | null;
  identity: ProductIdentity;
  variant_attributes: Record<string, string | number | boolean | null>;
  availability: string | null;
  retrieved_at: string | null;
  provenance: OfferProvenance | null;
  source: ProviderSource;
}

export interface ProviderMetadata {
  source: ProviderSource;
  provider: string;
  status: "ok" | "degraded" | "unavailable";
  fallback_reason: string | null;
  failure_reason: ProviderFailureReason | null;
}

export type TaskStatus =
  | "idle"
  | "starting"
  | "connecting"
  | "running"
  | "completed"
  | "cancelled"
  | "error";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "reconnecting" | "disconnected";

export type MonitorEventName =
  | "session_created"
  | "assistant_call"
  | "tool_start"
  | "tool_end"
  | "fork"
  | "task_result"
  | "task_cancelled"
  | "error";

export interface MarketplaceDemand {
  platform: Marketplace;
  query: string;
}

export interface ForkEventData {
  sub_thread_id: string;
  platform: Marketplace;
  demand: MarketplaceDemand;
  data_mode: DataMode;
}

export interface AssistantCallEventData {
  step: string;
  data_mode: DataMode;
  [key: string]: unknown;
}

export interface ToolEndEventData {
  tool_name: string;
  duration_ms: number;
  outcome: "success" | "degraded" | "failure";
  source: ProviderSource;
  provider: string;
  status: "ok" | "degraded" | "unavailable";
  fallback_reason: string | null;
  failure_reason: ProviderFailureReason | null;
  data_mode: DataMode;
}

export interface MonitorEventDataMap {
  session_created: { thread_id: string; reference_images: Record<string, unknown>[]; data_mode: DataMode };
  assistant_call: AssistantCallEventData;
  tool_start: { tool_name: string; args: Record<string, unknown>; data_mode: DataMode };
  tool_end: ToolEndEventData;
  fork: ForkEventData;
  task_result: TaskResultData;
  task_cancelled: { thread_id: string; data_mode: DataMode };
  error: { thread_id: string; code: string; data_mode: DataMode };
}

interface MonitorEventEnvelope<K extends MonitorEventName> {
  type: "monitor_event";
  event_id: string;
  thread_id: string;
  run_id: string;
  sequence: number;
  event: K;
  message: string;
  data: MonitorEventDataMap[K];
  timestamp: string;
}

export type MonitorEvent<K extends MonitorEventName = MonitorEventName> = K extends MonitorEventName
  ? MonitorEventEnvelope<K>
  : never;

export interface Recommendation extends ProductEvidence {
  image_url: string | null;
  price: number;
  currency: string;
  price_cny: number;
  shipping_cny: number;
  duty_cny: number;
  landed_cny: number;
  eta_days: number;
  rating: number | null;
  sales: number | null;
  attributes: Record<string, string | number | boolean | null>;
  note: string | null;
  duty_tier: "免征" | "标准" | "高税";
  shipping_estimate: EstimateDisclosure;
  duty_estimate: EstimateDisclosure;
  delivery_estimate: EstimateDisclosure;
  reason: string;
  rank: number;
  constraint_evaluations: ConstraintEvaluation[];
  score_breakdown: RankingScoreBreakdown;
}

export interface UnverifiedCandidate extends ProductEvidence {
  image_url: string | null;
  price: number;
  currency: string;
  price_cny: number;
  shipping_cny: number;
  duty_cny: number;
  landed_cny: number;
  eta_days: number;
  rating: number | null;
  sales: number | null;
  attributes: Record<string, string | number | boolean | null>;
  note: string | null;
  duty_tier: "免征" | "标准" | "高税";
  shipping_estimate: EstimateDisclosure;
  duty_estimate: EstimateDisclosure;
  delivery_estimate: EstimateDisclosure;
  reason: string;
  constraint_evaluations: ConstraintEvaluation[];
}

export interface ConstraintExclusion {
  item_id: string;
  platform: Marketplace;
  title: string;
  violated_count: number;
  violated_constraints: ConstraintEvaluation[];
}

export interface ConstraintRelaxationSuggestion {
  constraint: HardConstraint;
  suggestion: string;
  requires_confirmation: boolean;
}

export interface ComparisonItem extends ProductEvidence {
  price: number;
  price_cny: number;
  shipping_cny?: number;
  duty_cny?: number;
  landed_cny?: number;
  eta_days?: number;
  rating?: number | null;
  currency: string;
  price_local?: number;
  attributes: Record<string, string | number | boolean | null>;
  note?: string | null;
  shipping_estimate?: EstimateDisclosure;
  duty_estimate?: EstimateDisclosure;
  delivery_estimate?: EstimateDisclosure;
}

export interface GeneratedFile {
  name: string;
  url: string;
}

export interface TaskResultData {
  thread_id: string;
  final_answer: string;
  recommendations: Recommendation[];
  comparison: ComparisonItem[];
  files: GeneratedFile[];
  provider_mode: Exclude<ProviderMode, "unverified">;
  providers: Record<string, ProviderMetadata>;
  calculation_notice: string;
  exchange_rate: ExchangeRateProvenance;
  calculation_exclusions: CalculationExclusion[];
  ranking_profile: RankingProfile;
  data_mode: DataMode;
  result_kind: ResultKind;
  unavailable_marketplaces: Marketplace[];
  unverified_candidates?: UnverifiedCandidate[];
  exclusions?: ConstraintExclusion[];
  working_assumptions?: WorkingAssumption[];
  relaxation_suggestions?: ConstraintRelaxationSuggestion[];
  match_status?: "matched" | "no_match";
}

export interface TaskRequest {
  query: string;
  thread_id: string | null;
  user_id: string;
  upload_ids: string[];
}

export interface TaskStartResponse {
  status: "started";
  thread_id: string;
}

export interface TaskSnapshot {
  thread_id: string;
  run_id: string;
  status: "running" | "completed" | "cancelled" | "error";
  query: string;
  user_id: string;
  data_mode: DataMode;
  created_at: string;
  updated_at: string;
  events: MonitorEvent[];
  result: TaskResultData | null;
  error_code: string | null;
  error: string | null;
}

export interface TaskSnapshotMessage {
  type: "task_snapshot";
  snapshot: TaskSnapshot;
  timestamp: string;
}

export interface UploadResponse {
  upload_id: string;
  name?: string;
  filename?: string;
  url?: string;
}

export interface HealthResponse {
  status: "ok";
  service: "shopping-agent";
  version: string;
}

export interface ProviderCapability {
  configured: boolean;
  state: "configured" | "partial" | "missing";
  available: boolean;
  source: "live" | "fixture";
  failure_reason: ProviderFailureReason | null;
}

export interface ReadinessResponse {
  status: "ready" | "degraded" | "not_ready";
  task_ready: boolean;
  environment: "development" | "test" | "production";
  runtime_mode: "live" | "sandbox";
  agent_mode: "llm" | "rules" | "unavailable";
  requested_agent_mode: "auto" | "llm" | "rules";
  preference_store: "memory" | "redis";
  providers: Record<string, ProviderCapability>;
  capabilities: Record<string, boolean>;
  required_actions: string[];
  data_mode: DataMode;
  developer_diagnostic_mode: boolean;
}

export interface PreferencesResponse {
  user_id?: string;
  preferences?: unknown;
  items?: unknown;
}

export interface SessionHistoryItem {
  threadId: string;
  query: string;
  status: TaskStatus;
  createdAt: string;
  providerMode?: string;
}
