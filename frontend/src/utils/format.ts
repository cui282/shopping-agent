import type { MonitorEvent, TaskStatus } from "../types/api";
import {
  providerNameLabel,
  providerReasonLabel,
  providerSourceLabel,
  providerStatusLabel,
} from "./trust";

export const currencyCny = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatCount(value: number | null | undefined): string {
  if (value == null) return "暂无";
  if (value >= 10_000) return `${(value / 10_000).toFixed(value >= 100_000 ? 0 : 1)}万`;
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  const delta = Date.now() - date.getTime();
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

export function statusLabel(status: TaskStatus): string {
  const labels: Record<TaskStatus, string> = {
    idle: "等待研究",
    starting: "正在启动",
    connecting: "正在连接",
    running: "研究中",
    awaiting_clarification: "等待确认",
    completed: "已完成",
    cancelled: "已取消",
    error: "需要处理",
  };
  return labels[status];
}

export function eventMeta(event: MonitorEvent): { label: string; detail: string; note?: string } {
  const data = event.data as Record<string, unknown>;
  const tool = typeof data.tool_name === "string" ? data.tool_name : "";
  const stage = typeof data.step === "string" ? data.step : "";
  const toolLabels: Record<string, string> = {
    planner: "需求规划",
    category_insight: "类目洞察",
    web_search: "网页检索",
    item_search: "商品检索",
    price_compare: "价格换算",
    shipping_calc: "运税估算",
    item_picker: "候选筛选",
    recall: "候选召回",
    shopping_summary: "建议汇总",
  };
  const toolLabel = toolLabels[tool] ?? tool.replaceAll("_", " ");
  if (event.event === "fork") {
    const platform = typeof data.platform === "string" ? data.platform : "";
    const demand = data.demand && typeof data.demand === "object" ? (data.demand as Record<string, unknown>) : {};
    const query = typeof demand.query === "string" ? demand.query : "";
    return {
      label: platform ? `并行检索 · ${providerNameLabel(platform)}` : "并行检索",
      detail: query ? `需求：${query}` : event.message,
    };
  }
  if (event.event === "tool_end") {
    const duration = typeof data.duration_ms === "number" ? `${data.duration_ms} 毫秒` : "耗时未记录";
    const source =
      data.source === "live" || data.source === "curated" || data.source === "fixture" || data.source === "computed"
        ? providerSourceLabel(data.source)
        : "来源未记录";
    const status =
      data.status === "ok" || data.status === "degraded" || data.status === "unavailable"
        ? providerStatusLabel(data.status)
        : "状态未记录";
    const outcomeLabels = { success: "成功", degraded: "降级", failure: "失败" } as const;
    const outcome =
      data.outcome === "success" || data.outcome === "degraded" || data.outcome === "failure"
        ? outcomeLabels[data.outcome]
        : "结果未记录";
    const provider = typeof data.provider === "string" ? data.provider : "";
    const fallback = typeof data.fallback_reason === "string" ? providerReasonLabel(data.fallback_reason) : "";
    const recall = data.recall_provenance && typeof data.recall_provenance === "object"
      ? (data.recall_provenance as Record<string, unknown>)
      : null;
    const personalization = recall?.personalization && typeof recall.personalization === "object"
      ? (recall.personalization as Record<string, unknown>)
      : null;
    const personalizationState = typeof personalization?.state === "string" ? personalization.state : "";
    const personalizationReason = typeof personalization?.reason_code === "string" ? personalization.reason_code : "";
    const personalizationNote = personalization
      ? `个性化召回：${personalizationState === "ready" && personalization.participated ? "已生效" : personalizationState === "degraded" ? "已降级" : "未生效"}${personalizationReason ? ` · ${personalizationReason}` : ""}`
      : "";
    const note = [provider ? `数据提供方：${provider}` : "", fallback, personalizationNote].filter(Boolean).join(" · ");
    return {
      label: tool ? `${toolLabel}${outcome === "失败" ? "失败" : outcome === "降级" ? "已降级" : "已完成"}` : "处理已完成",
      detail: `${duration} · ${source} · ${status} · ${outcome}`,
      note: note || undefined,
    };
  }
  if (event.event === "context_compression") {
    const status = event.data.status === "applied"
      ? "已应用"
      : event.data.status === "degraded"
        ? "已降级"
        : "未触发";
    const retained = `${event.data.retained_message_count} 条最近消息`;
    const reason = event.data.reason_code ? ` · ${event.data.reason_code}` : "";
    return {
      label: `模型上下文${status}`,
      detail: `${retained} · 估算 ${event.data.estimated_tokens} tokens${reason}`,
    };
  }
  const labels: Record<MonitorEvent["event"], string> = {
    session_created: "会话已建立",
    intent_resolved: "意图与约束已保存",
    assistant_call: stage === "thinking" ? "正在分析需求" : "分析需求",
    context_compression: "模型上下文压缩",
    tool_start: tool ? `开始${toolLabel}` : "开始处理",
    tool_end: tool ? `${toolLabel}已完成` : "处理已完成",
    fork: "并行检索",
    report_generated: "研究报告已生成",
    task_result: "推荐已生成",
    task_cancelled: "研究已取消",
    clarification_required: "等待确认",
    clarification_resolved: "已收到确认",
    error: "流程中断",
  };
  const detail = event.message || labels[event.event];
  return { label: labels[event.event], detail };
}

export function flattenPreferences(value: unknown): string[] {
  if (value == null) return [];
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return [String(value)];
  }
  if (Array.isArray(value)) return value.flatMap(flattenPreferences);
  if (typeof value === "object") {
    const fieldLabels: Record<string, string> = {
      categories: "品类",
      category: "品类",
      materials: "材质",
      material_preferences: "材质",
      styles: "风格",
      style_preferences: "风格",
      hard_constraints: "硬性条件",
      soft_preferences: "偏好",
      avoid: "避开",
      destination: "收货地",
      budget_cny: "预算",
    };
    return Object.entries(value as Record<string, unknown>).flatMap(([key, item]) => {
      const values = flattenPreferences(item);
      const label = fieldLabels[key] ?? key.replaceAll("_", " ");
      return values.map((entry) => `${label}：${entry}`);
    });
  }
  return [];
}
