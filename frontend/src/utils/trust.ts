import type {
  DataMode,
  ProviderFailureReason,
  ProviderMode,
  ProviderSource,
  ReadinessComponentState,
  ReadinessResponse,
  RecallChannelName,
  RecallChannelState,
  RecallMode,
  ResultKind,
} from "../types/api";
import type { ServiceStatus } from "../hooks/useShoppingAgent";

export function providerModeLabel(mode: ProviderMode | string): string {
  const labels: Record<ProviderMode, string> = {
    live: "Live Result",
    mixed: "Developer Diagnostic · Mixed Source",
    sandbox: "Sandbox Result",
    unverified: "Result source pending",
  };
  return labels[mode as ProviderMode] ?? "Result source pending";
}

export function resultBadgeLabel(dataMode: DataMode | string, resultKind: ResultKind | string): string {
  if (dataMode === "mixed") return "Developer Diagnostic · Mixed Source";
  if (resultKind === "partial") return "Partial Result";
  return dataMode === "sandbox" ? "Sandbox Result" : "Live Result";
}

export function providerSourceLabel(source: ProviderSource): string {
  const labels: Record<ProviderSource, string> = {
    live: "数据提供商通道",
    curated: "策展资料",
    fixture: "沙盒样本",
    computed: "计算结果",
  };
  return labels[source];
}

export function providerStatusLabel(status: "ok" | "degraded" | "unavailable"): string {
  return status === "ok" ? "可用" : status === "degraded" ? "已降级" : "不可用";
}

export function providerReasonLabel(reason: string): string {
  if (reason === "SANDBOX_MODE is enabled") return "已显式启用沙盒模式";
  const stableLabels: Record<ProviderFailureReason, string> = {
    not_configured: "平台数据提供商通道未完整配置",
    request_failed: "平台数据提供商通道请求失败",
    empty_response: "平台数据提供商通道未返回可用商品证据",
    sandbox_forbidden: "生产环境拒绝沙盒数据",
  };
  if (reason in stableLabels) return stableLabels[reason as ProviderFailureReason];
  if (reason.startsWith("provider request failed:")) {
    return `平台请求失败（${reason.slice("provider request failed:".length).trim()}）`;
  }
  return reason;
}

export function taskDisabledReason(serviceStatus: ServiceStatus, readiness: ReadinessResponse | null): string | null {
  if (serviceStatus === "checking") return "正在检查服务配置";
  if (serviceStatus === "unavailable") return "无法确认服务配置，请先重试";
  if (!readiness?.task_ready) return "服务尚未配置，完成上方设置后可开始研究";
  return null;
}

export function requiredActionLabel(action: string): string {
  if (action.includes("OPENAI_API_KEY") || action.includes("ALLOW_RULES_FALLBACK")) {
    return "配置 OPENAI_API_KEY 与 LLM_MAIN，或启用规则编排回退";
  }
  if (action === "Disable SANDBOX_MODE in production") return "生产环境关闭 SANDBOX_MODE";
  if (action === "Disable DEVELOPER_DIAGNOSTIC_MODE in production") {
    return "生产环境关闭 DEVELOPER_DIAGNOSTIC_MODE";
  }
  if (action === "Enable DEVELOPER_DIAGNOSTIC_MODE to allow fixture fallback") {
    return "fixture fallback 仅能在显式开发诊断模式使用";
  }
  if (
    action.startsWith("Configure at least one data-provider marketplace channel") ||
    action.startsWith("Configure at least one marketplace")
  ) {
    return "至少配置一个数据提供商平台通道，本地验证也可显式启用 SANDBOX_MODE";
  }
  if (action.startsWith("Complete both ")) {
    return `补全数据提供商通道配置：${action.replace("Complete both ", "")}`;
  }
  if (action === "Use STORE_BACKEND=redis for persistent production preferences") {
    return "生产环境使用 STORE_BACKEND=redis 持久保存偏好";
  }
  if (action === "Redis preference backend unavailable; local evaluation is non-persistent") {
    return "Redis 偏好后端不可用；当前仅本地评估且不会持久保存";
  }
  if (action.startsWith("Configure OPENSEARCH_URL")) {
    return "配置 OPENSEARCH_URL 以启用 OpenSearch 类目知识召回";
  }
  if (action.startsWith("Enable ANN_BACKEND=faiss")) {
    return "启用 ANN_BACKEND=faiss 并配置 ANN_INDEX_PATH 以启用 ANN 召回";
  }
  if (action.startsWith("Configure ANN_INDEX_PATH")) {
    return "配置 ANN_INDEX_PATH 以启用 Faiss 召回";
  }
  if (action.startsWith("Configure TOWER_QUERY_ENDPOINT")) {
    return "配置 TOWER_QUERY_ENDPOINT 以启用 Query tower";
  }
  if (action.startsWith("Configure TOWER_ITEM_ENDPOINT")) {
    return "配置 TOWER_ITEM_ENDPOINT 以启用 Item tower";
  }
  if (action.startsWith("Configure TOWER_USER_ENDPOINT")) {
    return "配置 TOWER_USER_ENDPOINT 以启用个性化召回";
  }
  return action;
}

export function providerNameLabel(name: string): string {
  const labels: Record<string, string> = {
    web_search: "网页检索",
    category_insight: "类目洞察",
    amazon: "Amazon",
    shopee: "Shopee",
    aliexpress: "AliExpress",
    ebay: "eBay",
  };
  return labels[name] ?? name.replaceAll("_", " ");
}

export function recallChannelLabel(channel: RecallChannelName | string): string {
  const labels: Record<string, string> = {
    opensearch: "OpenSearch 类目知识",
    query_tower: "Query tower",
    item_tower: "Item tower",
    faiss: "Faiss ANN",
  };
  return labels[channel] ?? channel.replaceAll("_", " ");
}

export function recallModeLabel(mode: RecallMode | string): string {
  const labels: Record<string, string> = {
    hybrid: "Hybrid recall",
    partial_hybrid: "Partial hybrid recall",
    deterministic_fallback: "Deterministic fallback",
  };
  return labels[mode] ?? mode;
}

export function recallStateLabel(state: RecallChannelState | string): string {
  const labels: Record<string, string> = {
    configured: "已配置，尚未探测",
    ready: "已参与且可用",
    degraded: "已降级",
    unavailable: "不可用",
  };
  return labels[state] ?? state;
}

export function readinessComponentStateLabel(state: ReadinessComponentState | string): string {
  const labels: Record<string, string> = {
    ready: "已就绪",
    configured: "已配置，尚未探测",
    degraded: "已降级",
    unavailable: "不可用",
    disabled: "未启用",
  };
  return labels[state] ?? state;
}

export function readinessComponentLabel(name: string): string {
  const labels: Record<string, string> = {
    llm: "LLM",
    redis: "Redis",
    opensearch: "OpenSearch",
    faiss: "Faiss",
    query_tower: "Query tower",
    item_tower: "Item tower",
    user_tower: "User tower",
    storage: "Storage",
    image_analysis: "Image analysis",
  };
  if (name.startsWith("gateway.")) return `${providerNameLabel(name.slice("gateway.".length))} gateway`;
  return labels[name] ?? name.replaceAll("_", " ");
}

export function personalizationInputSourceLabel(source: string): string {
  if (source === "remembered_preference") return "显式 Remembered Preference";
  return "无保存偏好输入";
}

export function personalizationSignalLabel(signal: string): string {
  return signal === "user_tower" ? "User tower preference-match signal" : "未使用 user tower signal";
}
