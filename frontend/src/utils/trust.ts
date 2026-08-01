import type { DataMode, ProviderFailureReason, ProviderMode, ProviderSource, ReadinessResponse, ResultKind } from "../types/api";
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
    live: "实时商品",
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
    not_configured: "平台未配置完整网关",
    request_failed: "平台网关请求失败",
    empty_response: "平台未返回可用商品证据",
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
  if (action.startsWith("Configure at least one marketplace")) {
    return "至少配置一个平台的 API 地址与密钥，本地验证也可显式启用 SANDBOX_MODE";
  }
  if (action.startsWith("Complete both ")) {
    return `补全平台配置：${action.replace("Complete both ", "")}`;
  }
  if (action === "Use STORE_BACKEND=redis for persistent production preferences") {
    return "生产环境使用 STORE_BACKEND=redis 持久保存偏好";
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
