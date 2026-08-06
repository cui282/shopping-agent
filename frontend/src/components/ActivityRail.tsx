import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  BookmarkPlus,
  Check,
  ChevronRight,
  Circle,
  CircleHelp,
  GitFork,
  LoaderCircle,
  PanelRightClose,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import type { AgentState } from "../hooks/useShoppingAgent";
import type { MonitorEvent, PreferenceBackendStatus, PreferenceField } from "../types/api";
import { eventMeta, flattenPreferences } from "../utils/format";
import styles from "./ActivityRail.module.css";

const PIPELINE = [
  { name: "理解需求", tools: ["planner"] },
  { name: "类目洞察", tools: ["category_insight"] },
  { name: "跨平台检索", tools: ["web_search", "item_search"] },
  { name: "核算到手价", tools: ["price_compare", "shipping_calc"] },
  { name: "生成推荐", tools: ["item_picker", "shopping_summary"] },
];

function progressIndex(state: AgentState): number {
  if (state.status === "completed") return PIPELINE.length;
  let current = state.events.length ? 0 : -1;
  for (const event of state.events) {
    const tool = (event.data as Record<string, unknown>).tool_name;
    if (typeof tool !== "string") continue;
    PIPELINE.forEach((stage, index) => {
      if (stage.tools.includes(tool)) current = Math.max(current, index);
    });
  }
  return current;
}

function EventIcon({ event }: { event: MonitorEvent }) {
  if (event.event === "fork") return <GitFork size={14} aria-hidden="true" />;
  if (event.event === "error") return <AlertCircle size={14} aria-hidden="true" />;
  if (event.event === "clarification_required") return <CircleHelp size={14} aria-hidden="true" />;
  if (event.event === "tool_end" && event.data.outcome !== "success") {
    return <AlertCircle size={14} aria-hidden="true" />;
  }
  if (event.event === "task_result" || event.event === "tool_end") {
    return <Check size={14} aria-hidden="true" />;
  }
  return <ChevronRight size={14} aria-hidden="true" />;
}

interface ActivityRailProps {
  state: AgentState;
  userId: string;
  preferenceStore?: "memory" | "redis";
  preferenceBackend?: PreferenceBackendStatus;
  onClose: () => void;
}

const DEFAULT_BACKEND: PreferenceBackendStatus = {
  requested_backend: "memory",
  backend: "memory",
  durability: "local_evaluation",
  fallback_reason: null,
};

const preferenceFields: Array<{ value: PreferenceField; label: string }> = [
  { value: "style_preferences", label: "风格偏好" },
  { value: "material_preferences", label: "材质偏好" },
  { value: "soft_preferences", label: "使用偏好" },
  { value: "avoid", label: "避开" },
];

export default function ActivityRail({
  state,
  userId,
  preferenceStore,
  preferenceBackend,
  onClose,
}: ActivityRailProps) {
  const [preferences, setPreferences] = useState<string[]>([]);
  const [preferenceStatus, setPreferenceStatus] = useState<"loading" | "ready" | "error">("loading");
  const [confirmClear, setConfirmClear] = useState(false);
  const [preferenceField, setPreferenceField] = useState<PreferenceField>("style_preferences");
  const [preferenceValue, setPreferenceValue] = useState("");
  const [mutationStatus, setMutationStatus] = useState<
    "idle" | "saving" | "saved" | "cleared" | "failed"
  >("idle");
  const [backendStatus, setBackendStatus] = useState<PreferenceBackendStatus>(
    preferenceBackend ?? (preferenceStore === "redis" ? { ...DEFAULT_BACKEND, requested_backend: "redis", backend: "redis", durability: "durable" } : DEFAULT_BACKEND),
  );
  const eventListRef = useRef<HTMLOListElement>(null);
  const followTimelineRef = useRef(true);
  const preferenceRequestRef = useRef<AbortController | null>(null);
  const progress = progressIndex(state);
  const refreshKey = state.status === "completed" ? state.threadId : null;

  const loadPreferences = async () => {
    preferenceRequestRef.current?.abort();
    const controller = new AbortController();
    preferenceRequestRef.current = controller;
    setPreferenceStatus("loading");
    try {
      const response = await api.preferences(userId, { signal: controller.signal });
      if (preferenceRequestRef.current !== controller) return;
      setPreferences(flattenPreferences(response.preferences ?? response.items));
      if (response.backend) setBackendStatus(response.backend);
      setPreferenceStatus("ready");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setPreferenceStatus("error");
    } finally {
      if (preferenceRequestRef.current === controller) preferenceRequestRef.current = null;
    }
  };

  useEffect(() => {
    void loadPreferences();
    return () => preferenceRequestRef.current?.abort();
  }, [userId, refreshKey]);

  useEffect(() => {
    if (preferenceBackend) setBackendStatus(preferenceBackend);
  }, [preferenceBackend]);

  useEffect(() => {
    followTimelineRef.current = true;
    const list = eventListRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [state.threadId, state.runId]);

  useEffect(() => {
    const list = eventListRef.current;
    if (list && followTimelineRef.current) list.scrollTop = list.scrollHeight;
  }, [state.events.length]);

  const latestEvent = state.events.at(-1);

  const clear = async () => {
    try {
      const response = await api.clearPreferences(userId);
      setPreferences([]);
      setBackendStatus(response.backend);
      setPreferenceStatus("ready");
      setConfirmClear(false);
      setMutationStatus("cleared");
    } catch {
      setPreferenceStatus("error");
      setMutationStatus("failed");
    }
  };

  const savePreference = async () => {
    const value = preferenceValue.trim();
    if (!value) return;
    setMutationStatus("saving");
    try {
      const response = await api.updatePreferences(userId, {
        action: "remember",
        field: preferenceField,
        values: [value],
      });
      setPreferences(flattenPreferences(response.preferences ?? response.items));
      if (response.backend) setBackendStatus(response.backend);
      setPreferenceValue("");
      setMutationStatus("saved");
    } catch {
      setMutationStatus("failed");
    }
  };

  const backendLabel = backendStatus.durability === "durable" ? "Redis 持久偏好" : "本地评估，非持久偏好";

  return (
    <aside className={styles.rail} aria-label="研究过程与偏好">
      <div className={styles.topbar}>
        <div>
          <span className={styles.eyebrow}>实时进度</span>
          <h2>研究过程</h2>
        </div>
        <button className={styles.closeButton} type="button" onClick={onClose} aria-label="关闭过程面板" title="关闭过程面板">
          <PanelRightClose size={18} aria-hidden="true" />
        </button>
      </div>

      <ol className={styles.pipeline} aria-label="研究阶段">
        {PIPELINE.map((stage, index) => {
          const complete = index < progress || progress === PIPELINE.length;
          const active = index === progress && state.status === "running";
          const failed = index === progress && state.status === "error";
          const stageState = complete ? "已完成" : active ? "进行中" : failed ? "失败" : "待开始";
          return (
            <li
              key={stage.name}
              data-state={complete ? "complete" : active ? "active" : failed ? "error" : "pending"}
              aria-label={`${stage.name}，${stageState}`}
            >
              <span className={styles.stageIcon} aria-hidden="true">
                {complete ? (
                  <Check size={13} />
                ) : active ? (
                  <LoaderCircle className={styles.spinning} size={13} />
                ) : failed ? (
                  <AlertCircle size={13} />
                ) : (
                  <Circle size={9} />
                )}
              </span>
              <span>{stage.name}</span>
            </li>
          );
        })}
      </ol>

      <section className={styles.events} aria-labelledby="event-heading">
        <div className={styles.sectionHeading}>
          <h3 id="event-heading">进度记录</h3>
          <span>{state.events.length}</span>
        </div>
        {state.events.length ? (
          <ol
            ref={eventListRef}
            className={styles.eventList}
            aria-label="完整研究活动历史"
            tabIndex={0}
            onScroll={(event) => {
              const list = event.currentTarget;
              followTimelineRef.current = list.scrollHeight - list.scrollTop - list.clientHeight < 24;
            }}
          >
            {state.events.map((event) => {
              const meta = eventMeta(event);
              return (
                <li
                  key={event.event_id}
                  data-event={event.event}
                  data-outcome={event.event === "tool_end" ? event.data.outcome : undefined}
                >
                  <span className={styles.eventIcon}>
                    <EventIcon event={event} />
                  </span>
                  <span className={styles.eventText}>
                    <strong>{meta.label}</strong>
                    <small>{meta.detail}</small>
                    {meta.note && <small className={styles.eventNote}>{meta.note}</small>}
                  </span>
                  <time dateTime={event.timestamp}>
                    {new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(event.timestamp))}
                  </time>
                </li>
              );
            })}
          </ol>
        ) : (
          <div className={styles.eventEmpty}>
            <Circle size={14} aria-hidden="true" />
            等待第一条研究进度
          </div>
        )}
        <p className={styles.liveStatus} aria-live="polite" aria-atomic="true">
          {latestEvent ? `${eventMeta(latestEvent).label}：${eventMeta(latestEvent).detail}` : ""}
        </p>
      </section>

      <section className={styles.preferences} aria-labelledby="preferences-heading">
        <div className={styles.sectionHeading}>
          <h3 id="preferences-heading">偏好控制</h3>
          <button type="button" onClick={() => void loadPreferences()} aria-label="刷新偏好" title="刷新偏好">
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
        <p className={styles.backendStatus} data-durability={backendStatus.durability}>
          {backendLabel}
          {backendStatus.fallback_reason ? ` · ${backendStatus.fallback_reason}` : ""}
        </p>
        <div className={styles.preferenceEditor}>
          <label htmlFor="preference-field">保存到未来任务</label>
          <select
            id="preference-field"
            value={preferenceField}
            onChange={(event) => setPreferenceField(event.target.value as PreferenceField)}
            disabled={mutationStatus === "saving"}
          >
            {preferenceFields.map((field) => (
              <option value={field.value} key={field.value}>
                {field.label}
              </option>
            ))}
          </select>
          <label className={styles.visuallyHidden} htmlFor="preference-value">
            偏好值
          </label>
          <input
            id="preference-value"
            value={preferenceValue}
            onChange={(event) => setPreferenceValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void savePreference();
            }}
            placeholder="例如：简约"
            disabled={mutationStatus === "saving"}
          />
          <button
            className={styles.rememberButton}
            type="button"
            onClick={() => void savePreference()}
            disabled={!preferenceValue.trim() || mutationStatus === "saving"}
            aria-label="明确记住偏好"
            title="明确记住偏好"
          >
            <BookmarkPlus size={14} aria-hidden="true" />
            记住
          </button>
        </div>
        <p className={styles.preferenceFeedback} role="status" aria-live="polite">
          {mutationStatus === "saving"
            ? "正在保存偏好"
            : mutationStatus === "saved"
              ? "偏好已明确保存"
              : mutationStatus === "cleared"
                ? "偏好已清除"
                : mutationStatus === "failed"
                  ? "偏好操作失败，请重试"
                  : ""}
        </p>
        {preferenceStatus === "loading" ? (
          <p className={styles.preferencePending}>正在读取偏好</p>
        ) : preferenceStatus === "error" ? (
          <p className={styles.preferenceError}>暂时无法读取偏好。</p>
        ) : preferences.length ? (
          <ul className={styles.preferenceList}>
            {preferences.map((preference, index) => (
              <li key={`${preference}-${index}`}>{preference}</li>
            ))}
          </ul>
        ) : (
          <p className={styles.preferenceEmpty}>还没有记住的偏好。</p>
        )}

        {preferences.length > 0 && !confirmClear && (
          <button className={styles.clearTrigger} type="button" onClick={() => setConfirmClear(true)}>
            <Trash2 size={14} aria-hidden="true" /> 清除偏好
          </button>
        )}
        {confirmClear && (
          <div className={styles.confirmClear} role="group" aria-label="确认清除偏好">
            <span>清除后无法恢复</span>
            <button type="button" onClick={() => void clear()}>
              确认清除
            </button>
            <button type="button" onClick={() => setConfirmClear(false)}>
              保留
            </button>
          </div>
        )}
      </section>
    </aside>
  );
}
