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

export interface MonitorEvent<T = Record<string, unknown>> {
  type: "monitor_event";
  event: MonitorEventName;
  message: string;
  data: T;
  timestamp: string;
}

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
  status: string;
  query?: string;
  events?: MonitorEvent[];
  result?: TaskResultData | null;
  final_answer?: string;
  recommendations?: Recommendation[];
  comparison?: ComparisonItem[];
  files?: GeneratedFile[];
  provider_mode?: string;
  error?: string | null;
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
