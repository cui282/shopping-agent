import { AlertTriangle, CircleAlert, LoaderCircle, RefreshCw } from "lucide-react";
import type { AgentState } from "../hooks/useShoppingAgent";
import type { ReadinessComponentStatus } from "../types/api";
import {
  providerNameLabel,
  providerReasonLabel,
  recallChannelLabel,
  recallModeLabel,
  recallStateLabel,
  personalizationInputSourceLabel,
  requiredActionLabel,
  readinessComponentLabel,
  readinessComponentStateLabel,
} from "../utils/trust";
import styles from "./ReadinessNotice.module.css";

interface ReadinessNoticeProps {
  state: AgentState;
  onRefresh: () => void;
}

export default function ReadinessNotice({ state, onRefresh }: ReadinessNoticeProps) {
  const readiness = state.readiness;
  if (
    state.serviceStatus === "available" &&
    readiness?.status === "ready" &&
    readiness.data_mode !== "mixed"
  ) {
    return null;
  }

  if (state.serviceStatus === "checking") {
    return (
      <div className={styles.notice} data-state="checking" role="status">
        <LoaderCircle className={styles.spinning} size={17} aria-hidden="true" />
        <span>正在检查服务配置</span>
      </div>
    );
  }

  if (state.serviceStatus === "unavailable" || !readiness) {
    return (
      <div className={styles.notice} data-state="error" role="alert">
        <CircleAlert size={17} aria-hidden="true" />
        <div>
          <strong>无法读取服务配置</strong>
          <span>{state.serviceError ?? "请确认后端服务可访问后重试。"}</span>
        </div>
        <button type="button" onClick={onRefresh}>
          <RefreshCw size={15} aria-hidden="true" /> 重试
        </button>
      </div>
    );
  }

  const blocked = !readiness.task_ready;
  const title = blocked
    ? "服务尚未完成运行配置"
    : readiness.runtime_mode === "sandbox"
      ? "Sandbox Result 已启用"
      : readiness.data_mode === "mixed"
        ? "Developer Diagnostic mode 已启用"
        : "部分服务能力已降级";
  const actions = [
    ...readiness.required_actions,
    ...(readiness.recall?.required_actions ?? []),
  ].map(requiredActionLabel);
  const providers = Object.entries(readiness.providers);
  const recallChannels = readiness.recall ? Object.entries(readiness.recall.channels) : [];
  const personalization = readiness.recall?.personalization;
  const componentEntries: Array<[string, ReadinessComponentStatus]> = readiness.components
    ? [
        ["llm", readiness.components.llm],
        ...Object.entries(readiness.components.marketplace_gateways).map(([name, component]) => [
          `gateway.${name}`,
          component,
        ] as [string, ReadinessComponentStatus]),
        ["redis", readiness.components.redis],
        ["opensearch", readiness.components.opensearch],
        ["faiss", readiness.components.faiss],
        ["query_tower", readiness.components.query_tower],
        ["item_tower", readiness.components.item_tower],
        ["user_tower", readiness.components.user_tower],
        ["storage", readiness.components.storage],
        ["image_analysis", readiness.components.image_analysis],
      ]
    : [];

  return (
    <div className={styles.notice} data-state={blocked ? "error" : "warning"} role={blocked ? "alert" : "status"}>
      <AlertTriangle size={17} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <span>
          {blocked
            ? "完成以下配置后才能启动购物研究。"
            : "任务仍可运行，结果页会逐项标明 Live、Sandbox 或 Partial Result。"}
        </span>
        {actions.length > 0 && (
          <ul>
            {actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        )}
        {providers.length > 0 && (
          <ul className={styles.providers} aria-label="平台 readiness">
            {providers.map(([name, capability]) => (
              <li key={name} data-available={capability.available}>
                <strong>{providerNameLabel(name)}</strong>
                <span>
                  {capability.available
                    ? capability.source === "fixture"
                      ? "sandbox fixture 可用"
                      : capability.state === "configured"
                        ? "live gateway 已配置，尚未探测"
                        : "live gateway 配置不完整"
                    : providerReasonLabel(capability.failure_reason ?? capability.state)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {componentEntries.length > 0 && (
          <details className={styles.componentDisclosure}>
            <summary>运行组件状态</summary>
            <ul className={styles.components} aria-label="运行组件状态">
              {componentEntries.map(([name, component]) => (
                <li key={name} data-ready={component.ready} data-state={component.state}>
                  <strong>{readinessComponentLabel(name)}</strong>
                  <span>{readinessComponentStateLabel(component.state)} · {component.reason_code}</span>
                  <small>{component.reason}</small>
                </li>
              ))}
            </ul>
          </details>
        )}
        {readiness.recall && (
          <details className={styles.recallDisclosure}>
            <summary>召回状态：{recallModeLabel(readiness.recall.mode)}</summary>
            <span>可选 channel 缺失时保留 deterministic fallback。</span>
            {recallChannels.length > 0 && (
              <ul className={styles.providers} aria-label="召回 channel readiness">
                {recallChannels.map(([name, channel]) => (
                  <li key={name} data-available={channel.state === "ready"}>
                    <strong>{recallChannelLabel(name)}</strong>
                    <span>{recallStateLabel(channel.state)} · {channel.reason_code}</span>
                    {channel.state !== "ready" && <small>{channel.reason}</small>}
                  </li>
                ))}
              </ul>
            )}
            {personalization && (
              <div className={styles.personalization} data-state={personalization.state}>
                <strong>
                  个性化召回：{personalization.state === "ready" && personalization.participated
                    ? "已生效"
                    : personalization.state === "degraded"
                      ? "已降级"
                      : "未生效"}
                </strong>
                <span>
                  输入来源：{personalizationInputSourceLabel(personalization.input_source)} · {personalization.reason_code}
                </span>
                <small>{personalization.reason}</small>
              </div>
            )}
          </details>
        )}
      </div>
      <button type="button" onClick={onRefresh}>
        <RefreshCw size={15} aria-hidden="true" /> 重新检查
      </button>
    </div>
  );
}
