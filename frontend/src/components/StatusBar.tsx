import { AlertCircle, CheckCircle2, CircleHelp, Cloud, CloudOff, LoaderCircle, Radio } from "lucide-react";
import type { AgentState } from "../hooks/useShoppingAgent";
import { statusLabel } from "../utils/format";
import styles from "./StatusBar.module.css";

export default function StatusBar({ state, onReconnect }: { state: AgentState; onReconnect: () => void }) {
  const active = ["starting", "connecting", "running"].includes(state.status);
  const StatusIcon =
    state.status === "completed"
      ? CheckCircle2
      : state.status === "awaiting_clarification"
        ? CircleHelp
      : state.status === "error"
        ? AlertCircle
        : active
          ? LoaderCircle
          : Radio;
  const connectionCopy =
    state.connection === "reconnecting"
      ? "实时连接恢复中"
      : state.connection === "disconnected"
        ? "实时连接已断开"
        : state.connection === "connected"
          ? "实时同步"
          : "";

  return (
    <div className={styles.bar} role="status" aria-live="polite">
      <span className={styles.task} data-status={state.status}>
        <StatusIcon className={active ? styles.spinning : undefined} size={15} aria-hidden="true" />
        {statusLabel(state.status)}
      </span>
      {connectionCopy && (
        <span className={styles.connection} data-disconnected={state.connection === "disconnected"}>
          {state.connection === "disconnected" ? <CloudOff size={14} aria-hidden="true" /> : <Cloud size={14} aria-hidden="true" />}
          {connectionCopy}
        </span>
      )}
      {state.connection === "disconnected" && state.threadId && (
        <button type="button" className={styles.retry} onClick={onReconnect}>
          重新连接
        </button>
      )}
    </div>
  );
}
