import { useCallback, useEffect, useReducer, useRef } from "react";
import { api, buildWebSocketUrl } from "../api/client";
import type {
  ConnectionStatus,
  HealthResponse,
  MonitorEvent,
  ProviderMode,
  ReadinessResponse,
  TaskRequest,
  TaskResultData,
  TaskSnapshot,
  TaskStatus,
} from "../types/api";
import { socketCloseDecision } from "../utils/socketLifecycle";

const TERMINAL_EVENTS = new Set(["task_result", "task_cancelled", "error"]);
const MAX_RECONNECTS = 5;
const HEARTBEAT_INTERVAL_MS = 20_000;
const RECONNECT_STABLE_MS = 5_000;

export type ServiceStatus = "checking" | "available" | "unavailable";

export interface AgentState {
  threadId: string | null;
  query: string;
  status: TaskStatus;
  connection: ConnectionStatus;
  events: MonitorEvent[];
  result: TaskResultData | null;
  error: string | null;
  providerMode: ProviderMode;
  health: HealthResponse | null;
  readiness: ReadinessResponse | null;
  serviceStatus: ServiceStatus;
  serviceError: string | null;
}

export const initialAgentState: AgentState = {
  threadId: null,
  query: "",
  status: "idle",
  connection: "idle",
  events: [],
  result: null,
  error: null,
  providerMode: "unverified",
  health: null,
  readiness: null,
  serviceStatus: "checking",
  serviceError: null,
};

function eventFingerprint(event: MonitorEvent): string {
  const data = event.data as Record<string, unknown>;
  const tool = typeof data.tool_name === "string" ? data.tool_name : "";
  const thread = typeof data.thread_id === "string" ? data.thread_id : "";
  return [event.event, event.timestamp, tool, thread, event.message].join("|");
}

type Action =
  | { type: "service_checking" }
  | { type: "service_loaded"; health: HealthResponse; readiness: ReadinessResponse }
  | { type: "service_failure"; message: string }
  | { type: "starting"; query: string }
  | { type: "started"; threadId: string }
  | { type: "connection"; connection: ConnectionStatus }
  | { type: "event"; event: MonitorEvent }
  | { type: "cancelled" }
  | { type: "failure"; message: string }
  | { type: "snapshot"; snapshot: TaskSnapshot; fallbackQuery?: string; preserveEvents?: boolean }
  | { type: "reset" };

export function agentReducer(state: AgentState, action: Action): AgentState {
  switch (action.type) {
    case "service_checking":
      return { ...state, serviceStatus: "checking", serviceError: null };
    case "service_loaded":
      return {
        ...state,
        health: action.health,
        readiness: action.readiness,
        serviceStatus: "available",
        serviceError: null,
      };
    case "service_failure":
      return {
        ...state,
        health: null,
        readiness: null,
        serviceStatus: "unavailable",
        serviceError: action.message,
      };
    case "starting":
      return {
        ...state,
        query: action.query,
        status: "starting",
        connection: "idle",
        threadId: null,
        events: [],
        result: null,
        error: null,
        providerMode: "unverified",
      };
    case "started":
      return { ...state, threadId: action.threadId, status: "connecting", connection: "connecting" };
    case "connection":
      return {
        ...state,
        connection: action.connection,
        status: state.status === "connecting" && action.connection === "connected" ? "running" : state.status,
      };
    case "event": {
      const event = action.event;
      const fingerprint = eventFingerprint(event);
      if (state.events.some((existing) => eventFingerprint(existing) === fingerprint)) return state;
      const result = event.event === "task_result" ? (event.data as unknown as TaskResultData) : state.result;
      const nextStatus: TaskStatus =
        event.event === "task_result"
          ? "completed"
          : event.event === "task_cancelled"
            ? "cancelled"
            : event.event === "error"
              ? "error"
              : "running";
      const message = event.event === "error" ? event.message || "研究流程未能完成" : state.error;
      return {
        ...state,
        events: [...state.events, event],
        status: nextStatus,
        result,
        error: message,
        providerMode: result ? normalizeProviderMode(result.provider_mode) : state.providerMode,
      };
    }
    case "cancelled":
      return { ...state, status: "cancelled", connection: "idle", error: null };
    case "failure":
      return { ...state, status: "error", connection: "disconnected", error: action.message };
    case "snapshot": {
      const snapshot = action.snapshot;
      const result = snapshot.result ?? null;
      const normalized = normalizeSnapshotStatus(snapshot.status, Boolean(result));
      return {
        ...state,
        threadId: snapshot.thread_id,
        query: snapshot.query ?? action.fallbackQuery ?? state.query,
        status: normalized,
        connection: normalized === "running" ? state.connection : "idle",
        events: snapshot.events ?? (action.preserveEvents ? state.events : []),
        result,
        providerMode: normalizeProviderMode(result?.provider_mode ?? snapshot.provider_mode),
        error: snapshot.error ?? null,
      };
    }
    case "reset":
      return {
        ...initialAgentState,
        health: state.health,
        readiness: state.readiness,
        serviceStatus: state.serviceStatus,
        serviceError: state.serviceError,
      };
  }
}

function normalizeProviderMode(value: unknown): ProviderMode {
  return value === "live" || value === "mixed" || value === "sandbox" ? value : "unverified";
}

function normalizeSnapshotStatus(status: string, hasResult: boolean): TaskStatus {
  if (hasResult || ["completed", "complete", "done", "success"].includes(status)) return "completed";
  if (["cancelled", "canceled"].includes(status)) return "cancelled";
  if (["error", "failed"].includes(status)) return "error";
  if (["running", "started", "pending"].includes(status)) return "running";
  return "idle";
}

function parseMonitorEvent(raw: string): MonitorEvent | null {
  try {
    const value = JSON.parse(raw) as Partial<MonitorEvent>;
    if (value.type !== "monitor_event" || !value.event || !value.timestamp) return null;
    return {
      type: "monitor_event",
      event: value.event,
      message: value.message ?? "",
      data: value.data ?? {},
      timestamp: value.timestamp,
    } as MonitorEvent;
  } catch {
    return null;
  }
}

interface SnapshotSyncOptions {
  threadId: string;
  fallbackQuery?: string;
  signal?: AbortSignal;
  request: (threadId: string, signal?: AbortSignal) => Promise<TaskSnapshot>;
  isCurrent: () => boolean;
  apply: (snapshot: TaskSnapshot, fallbackQuery?: string) => void;
}

export async function runSnapshotSync(options: SnapshotSyncOptions): Promise<void> {
  try {
    const snapshot = await options.request(options.threadId, options.signal);
    if (!options.isCurrent()) return;
    options.apply(snapshot, options.fallbackQuery);
  } catch {
    // The WebSocket error remains primary; a missing snapshot should not replace it.
  }
}

export function useShoppingAgent() {
  const [state, dispatch] = useReducer(agentReducer, initialAgentState);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const heartbeatTimerRef = useRef<number | null>(null);
  const stableTimerRef = useRef<number | null>(null);
  const reconnectCountRef = useRef(0);
  const socketGenerationRef = useRef(0);
  const terminalRef = useRef(false);
  const disposedRef = useRef(false);
  const startRequestRef = useRef<AbortController | null>(null);
  const loadRequestRef = useRef<AbortController | null>(null);
  const cancelRequestRef = useRef<AbortController | null>(null);
  const serviceRequestRef = useRef<AbortController | null>(null);
  const snapshotSyncRequestRef = useRef<AbortController | null>(null);
  const taskIntentRef = useRef<{ generation: number; threadId: string | null }>({ generation: 0, threadId: null });

  const replaceTaskIntent = useCallback((threadId: string | null) => {
    snapshotSyncRequestRef.current?.abort();
    snapshotSyncRequestRef.current = null;
    taskIntentRef.current = {
      generation: taskIntentRef.current.generation + 1,
      threadId,
    };
  }, []);

  const closeSocket = useCallback(() => {
    socketGenerationRef.current += 1;
    if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
    if (heartbeatTimerRef.current != null) window.clearInterval(heartbeatTimerRef.current);
    if (stableTimerRef.current != null) window.clearTimeout(stableTimerRef.current);
    reconnectTimerRef.current = null;
    heartbeatTimerRef.current = null;
    stableTimerRef.current = null;
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "client reset");
  }, []);

  const syncSnapshot = useCallback(async (threadId: string, fallbackQuery?: string) => {
    snapshotSyncRequestRef.current?.abort();
    const controller = new AbortController();
    snapshotSyncRequestRef.current = controller;
    const intent = taskIntentRef.current;
    if (intent.threadId !== threadId) {
      snapshotSyncRequestRef.current = null;
      return;
    }
    try {
      await runSnapshotSync({
        threadId,
        fallbackQuery,
        signal: controller.signal,
        request: (requestedThreadId, signal) => api.taskSnapshot(requestedThreadId, { signal }),
        isCurrent: () =>
          snapshotSyncRequestRef.current === controller &&
          !disposedRef.current &&
          taskIntentRef.current.generation === intent.generation &&
          taskIntentRef.current.threadId === threadId,
        apply: (snapshot, query) =>
          dispatch({ type: "snapshot", snapshot, fallbackQuery: query, preserveEvents: true }),
      });
    } finally {
      if (snapshotSyncRequestRef.current === controller) snapshotSyncRequestRef.current = null;
    }
  }, []);

  const connect = useCallback(
    function openSocket(threadId: string, fallbackQuery?: string) {
      if (disposedRef.current || terminalRef.current) return;
      if (reconnectTimerRef.current != null) window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
      const generation = socketGenerationRef.current + 1;
      socketGenerationRef.current = generation;
      const current = socketRef.current;
      if (current && current.readyState < WebSocket.CLOSING) current.close(1000, "replaced");

      dispatch({ type: "connection", connection: reconnectCountRef.current ? "reconnecting" : "connecting" });
      const socket = new WebSocket(buildWebSocketUrl(threadId));
      socketRef.current = socket;

      socket.onopen = () => {
        if (generation !== socketGenerationRef.current || socketRef.current !== socket) {
          socket.close(1000, "superseded");
          return;
        }
        dispatch({ type: "connection", connection: "connected" });
        if (heartbeatTimerRef.current != null) window.clearInterval(heartbeatTimerRef.current);
        if (stableTimerRef.current != null) window.clearTimeout(stableTimerRef.current);
        heartbeatTimerRef.current = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) socket.send("ping");
        }, HEARTBEAT_INTERVAL_MS);
        stableTimerRef.current = window.setTimeout(() => {
          if (generation === socketGenerationRef.current && socket.readyState === WebSocket.OPEN) {
            reconnectCountRef.current = 0;
          }
        }, RECONNECT_STABLE_MS);
      };
      socket.onmessage = (message) => {
        if (generation !== socketGenerationRef.current || socketRef.current !== socket) return;
        if (typeof message.data !== "string") return;
        if (message.data === "pong") return;
        const event = parseMonitorEvent(message.data);
        if (!event) return;
        dispatch({ type: "event", event });
        if (TERMINAL_EVENTS.has(event.event)) {
          terminalRef.current = true;
          if (heartbeatTimerRef.current != null) window.clearInterval(heartbeatTimerRef.current);
          heartbeatTimerRef.current = null;
          window.setTimeout(() => {
            if (generation === socketGenerationRef.current) socket.close(1000, "task complete");
          }, 80);
        }
      };
      socket.onerror = () => {
        if (generation === socketGenerationRef.current) socket.close();
      };
      socket.onclose = () => {
        const decision = socketCloseDecision({
          generation,
          currentGeneration: socketGenerationRef.current,
          disposed: disposedRef.current,
          terminal: terminalRef.current,
          reconnectCount: reconnectCountRef.current,
          maxReconnects: MAX_RECONNECTS,
        });
        if (decision === "ignore") return;
        if (heartbeatTimerRef.current != null) window.clearInterval(heartbeatTimerRef.current);
        if (stableTimerRef.current != null) window.clearTimeout(stableTimerRef.current);
        heartbeatTimerRef.current = null;
        stableTimerRef.current = null;
        if (socketRef.current === socket) socketRef.current = null;
        if (decision === "settle") {
          dispatch({ type: "connection", connection: "idle" });
          return;
        }
        if (decision === "sync") {
          dispatch({ type: "connection", connection: "disconnected" });
          void syncSnapshot(threadId, fallbackQuery);
          return;
        }
        reconnectCountRef.current += 1;
        dispatch({ type: "connection", connection: "reconnecting" });
        const delay = Math.min(800 * 2 ** (reconnectCountRef.current - 1), 8_000);
        reconnectTimerRef.current = window.setTimeout(() => openSocket(threadId, fallbackQuery), delay);
      };
    },
    [syncSnapshot],
  );

  const startTask = useCallback(
    async (payload: Omit<TaskRequest, "thread_id"> & { thread_id?: string | null }) => {
      if (!state.readiness?.task_ready) return null;
      loadRequestRef.current?.abort();
      loadRequestRef.current = null;
      cancelRequestRef.current?.abort();
      cancelRequestRef.current = null;
      replaceTaskIntent(null);
      startRequestRef.current?.abort();
      closeSocket();
      terminalRef.current = false;
      reconnectCountRef.current = 0;
      dispatch({ type: "starting", query: payload.query });
      const intentGeneration = taskIntentRef.current.generation;
      const controller = new AbortController();
      startRequestRef.current = controller;
      try {
        const response = await api.startTask(
          { ...payload, thread_id: payload.thread_id ?? null },
          { signal: controller.signal },
        );
        if (
          startRequestRef.current !== controller ||
          disposedRef.current ||
          taskIntentRef.current.generation !== intentGeneration
        ) {
          return null;
        }
        taskIntentRef.current = { ...taskIntentRef.current, threadId: response.thread_id };
        dispatch({ type: "started", threadId: response.thread_id });
        connect(response.thread_id, payload.query);
        return response.thread_id;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return null;
        dispatch({ type: "failure", message: error instanceof Error ? error.message : "无法启动研究任务" });
        return null;
      } finally {
        if (startRequestRef.current === controller) startRequestRef.current = null;
      }
    },
    [closeSocket, connect, replaceTaskIntent, state.readiness?.task_ready],
  );

  const cancelTask = useCallback(async () => {
    if (!state.threadId) return;
    cancelRequestRef.current?.abort();
    const controller = new AbortController();
    cancelRequestRef.current = controller;
    try {
      const response = await api.cancelTask(state.threadId, { signal: controller.signal });
      if (cancelRequestRef.current !== controller || disposedRef.current) return;
      if (response.status === "cancelled" || response.status === "canceled") {
        terminalRef.current = true;
        replaceTaskIntent(state.threadId);
        closeSocket();
        dispatch({ type: "cancelled" });
      } else {
        await syncSnapshot(state.threadId, state.query);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      dispatch({ type: "failure", message: error instanceof Error ? error.message : "取消任务失败" });
    } finally {
      if (cancelRequestRef.current === controller) cancelRequestRef.current = null;
    }
  }, [closeSocket, replaceTaskIntent, state.query, state.threadId, syncSnapshot]);

  const loadThread = useCallback(
    async (threadId: string, fallbackQuery: string) => {
      startRequestRef.current?.abort();
      loadRequestRef.current?.abort();
      cancelRequestRef.current?.abort();
      replaceTaskIntent(threadId);
      closeSocket();
      terminalRef.current = false;
      dispatch({ type: "starting", query: fallbackQuery });
      const controller = new AbortController();
      loadRequestRef.current = controller;
      const intentGeneration = taskIntentRef.current.generation;
      try {
        const snapshot = await api.taskSnapshot(threadId, { signal: controller.signal });
        if (
          loadRequestRef.current !== controller ||
          disposedRef.current ||
          taskIntentRef.current.generation !== intentGeneration ||
          taskIntentRef.current.threadId !== threadId
        ) {
          return;
        }
        dispatch({ type: "snapshot", snapshot, fallbackQuery });
        if (normalizeSnapshotStatus(snapshot.status, Boolean(snapshot.result)) === "running") connect(threadId, fallbackQuery);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        dispatch({ type: "failure", message: error instanceof Error ? error.message : "无法读取这次研究" });
      } finally {
        if (loadRequestRef.current === controller) loadRequestRef.current = null;
      }
    },
    [closeSocket, connect, replaceTaskIntent],
  );

  const reset = useCallback(() => {
    startRequestRef.current?.abort();
    startRequestRef.current = null;
    loadRequestRef.current?.abort();
    loadRequestRef.current = null;
    cancelRequestRef.current?.abort();
    cancelRequestRef.current = null;
    replaceTaskIntent(null);
    closeSocket();
    terminalRef.current = true;
    dispatch({ type: "reset" });
  }, [closeSocket, replaceTaskIntent]);

  const refreshReadiness = useCallback(async () => {
    serviceRequestRef.current?.abort();
    const controller = new AbortController();
    serviceRequestRef.current = controller;
    dispatch({ type: "service_checking" });
    try {
      const [health, readiness] = await Promise.all([
        api.health({ signal: controller.signal }),
        api.readiness({ signal: controller.signal }),
      ]);
      if (serviceRequestRef.current !== controller || disposedRef.current) return;
      dispatch({ type: "service_loaded", health, readiness });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (!disposedRef.current) {
        dispatch({
          type: "service_failure",
          message: error instanceof Error ? error.message : "无法读取服务配置",
        });
      }
    } finally {
      if (serviceRequestRef.current === controller) serviceRequestRef.current = null;
    }
  }, []);

  const reconnect = useCallback(
    (threadId: string, fallbackQuery?: string) => {
      closeSocket();
      terminalRef.current = false;
      reconnectCountRef.current = 0;
      connect(threadId, fallbackQuery);
    },
    [closeSocket, connect],
  );

  useEffect(() => {
    disposedRef.current = false;
    void refreshReadiness();
    return () => {
      disposedRef.current = true;
      serviceRequestRef.current?.abort();
      startRequestRef.current?.abort();
      loadRequestRef.current?.abort();
      cancelRequestRef.current?.abort();
      snapshotSyncRequestRef.current?.abort();
      closeSocket();
    };
  }, [closeSocket, refreshReadiness]);

  return { state, startTask, cancelTask, loadThread, reset, reconnect, refreshReadiness };
}
