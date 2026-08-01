import { AlertTriangle, CircleAlert, LoaderCircle, RefreshCw } from "lucide-react";
import type { AgentState } from "../hooks/useShoppingAgent";
import { providerNameLabel, providerReasonLabel, requiredActionLabel } from "../utils/trust";
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
  const actions = readiness.required_actions.map(requiredActionLabel);
  const providers = Object.entries(readiness.providers);

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
                      : "live gateway 已配置"
                    : providerReasonLabel(capability.failure_reason ?? capability.state)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <button type="button" onClick={onRefresh}>
        <RefreshCw size={15} aria-hidden="true" /> 重新检查
      </button>
    </div>
  );
}
