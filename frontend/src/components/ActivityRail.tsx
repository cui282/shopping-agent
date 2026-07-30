import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  ChevronRight,
  Circle,
  GitFork,
  LoaderCircle,
  PanelRightClose,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import type { AgentState } from "../hooks/useShoppingAgent";
import type { MonitorEvent } from "../types/api";
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
  if (event.event === "task_result" || event.event === "tool_end") return <Check size={14} aria-hidden="true" />;
  return <ChevronRight size={14} aria-hidden="true" />;
}

interface ActivityRailProps {
  state: AgentState;
  userId: string;
  preferenceStore?: "memory" | "redis";
  onClose: () => void;
}

export default function ActivityRail({ state, userId, preferenceStore, onClose }: ActivityRailProps) {
  const [preferences, setPreferences] = useState<string[]>([]);
  const [preferenceStatus, setPreferenceStatus] = useState<"loading" | "ready" | "error">("loading");
  const [confirmClear, setConfirmClear] = useState(false);
  const eventListRef = useRef<HTMLOListElement>(null);
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
    const list = eventListRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [state.events.length]);

  const visibleEvents = useMemo(() => state.events.slice(-18), [state.events]);
  const latestEvent = visibleEvents.at(-1);

  const clear = async () => {
    try {
      await api.clearPreferences(userId);
      setPreferences([]);
      setPreferenceStatus("ready");
      setConfirmClear(false);
    } catch {
      setPreferenceStatus("error");
    }
  };

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
        {visibleEvents.length ? (
          <ol ref={eventListRef} className={styles.eventList}>
            {visibleEvents.map((event, index) => {
              const meta = eventMeta(event);
              return (
                <li key={`${event.timestamp}-${index}`} data-event={event.event}>
                  <span className={styles.eventIcon}>
                    <EventIcon event={event} />
                  </span>
                  <span className={styles.eventText}>
                    <strong>{meta.label}</strong>
                    <small>{meta.detail}</small>
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
          <h3 id="preferences-heading">{preferenceStore === "redis" ? "长期偏好" : "服务内偏好"}</h3>
          <button type="button" onClick={() => void loadPreferences()} aria-label="刷新偏好" title="刷新偏好">
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
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
