export type Marketplace = "amazon" | "shopee" | "aliexpress" | "ebay" | string;

export type ProviderSource = "live" | "curated" | "fixture" | "computed";
export type ProviderMode = "live" | "mixed" | "sandbox" | "unverified";

export interface ProviderMetadata {
  source: ProviderSource;
  provider: string;
  status: "ok" | "degraded" | "unavailable";
  fallback_reason: string | null;
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
}

export interface ToolEndEventData {
  tool_name: string;
  duration_ms: number;
  outcome: "success" | "degraded" | "failure";
  source: ProviderSource;
  provider: string;
  status: "ok" | "degraded" | "unavailable";
  fallback_reason: string | null;
}

export interface MonitorEventDataMap {
  session_created: { thread_id: string; reference_images: Record<string, unknown>[] };
  assistant_call: Record<string, unknown>;
  tool_start: { tool_name: string; args: Record<string, unknown> };
  tool_end: ToolEndEventData;
  fork: ForkEventData;
  task_result: TaskResultData;
  task_cancelled: { thread_id: string };
  error: { thread_id: string; code: string };
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

export interface Recommendation {
  item_id: string;
  platform: Marketplace;
  title: string;
  image_url: string | null;
  product_url: string | null;
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
  source: ProviderSource;
  note: string | null;
  duty_tier: "免征" | "标准" | "高税";
  reason: string;
  rank: number;
}

export interface ComparisonItem {
  item_id: string;
  platform: Marketplace;
  title: string;
  price_cny: number;
  shipping_cny?: number;
  duty_cny?: number;
  landed_cny?: number;
  eta_days?: number;
  rating?: number | null;
  currency?: string;
  price_local?: number;
  source: ProviderSource;
  note?: string | null;
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
