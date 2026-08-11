import { useCallback, useEffect, useReducer, useRef } from "react";
import { ApiError, api, buildWebSocketUrl } from "../api/client";
import type {
  ConnectionStatus,
  ClarificationPrompt,
  HealthResponse,
  MonitorEvent,
  ProviderMode,
  ReadinessResponse,
  TaskRequest,
  TaskResultData,
  TaskSnapshot,
  TaskSnapshotMessage,
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
  runId: string | null;
  query: string;
  status: TaskStatus;
  connection: ConnectionStatus;
  events: MonitorEvent[];
  result: TaskResultData | null;
  snapshot: TaskSnapshot | null;
  clarification: ClarificationPrompt | null;
  error: string | null;
  providerMode: ProviderMode;
  health: HealthResponse | null;
  readiness: ReadinessResponse | null;
  serviceStatus: ServiceStatus;
  serviceError: string | null;
  loadingSnapshot: boolean;
  loadError: string | null;
  snapshotFallbackStatus: TaskStatus | null;
}

export const initialAgentState: AgentState = {
  threadId: null,
  runId: null,
  query: "",
  status: "idle",
  connection: "idle",
  events: [],
  result: null,
  snapshot: null,
  clarification: null,
  error: null,
  providerMode: "unverified",
  health: null,
  readiness: null,
  serviceStatus: "checking",
  serviceError: null,
  loadingSnapshot: false,
  loadError: null,
  snapshotFallbackStatus: null,
};

function mergeEvents(...timelines: MonitorEvent[][]): MonitorEvent[] {
  const merged = new Map<string, MonitorEvent>();
  for (const timeline of timelines) {
    for (const event of timeline) {
      if (!merged.has(event.event_id)) merged.set(event.event_id, event);
    }
  }
  return [...merged.values()].sort(
    (left, right) => left.sequence - right.sequence || left.event_id.localeCompare(right.event_id),
  );
}

function lastTimelineEvent(
  events: MonitorEvent[],
  predicate: (event: MonitorEvent) => boolean,
): MonitorEvent | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (predicate(events[index])) return events[index];
  }
  return undefined;
}

function terminalState(events: MonitorEvent[]): TaskStatus | null {
  const terminal = lastTimelineEvent(events, (event) => TERMINAL_EVENTS.has(event.event));
  if (terminal?.event === "task_result") return "completed";
  if (terminal?.event === "task_cancelled") return "cancelled";
  if (terminal?.event === "error") return "error";
  return null;
}

function timelineResult(events: MonitorEvent[]): TaskResultData | null {
  const result = lastTimelineEvent(events, (event) => event.event === "task_result");
  return result ? (result.data as TaskResultData) : null;
}

function timelineError(events: MonitorEvent[]): string | null {
  return lastTimelineEvent(events, (event) => event.event === "error")?.message ?? null;
}

function timelineClarification(events: MonitorEvent[]): ClarificationPrompt | null {
  let pending: ClarificationPrompt | null = null;
  for (const event of events) {
    if (event.event === "clarification_required") {
      pending = {
        field: event.data.field,
        reason_code: event.data.reason_code,
        question: event.data.question,
      };
    } else if (event.event === "clarification_resolved" || TERMINAL_EVENTS.has(event.event)) {
      pending = null;
    }
  }
  return pending;
}

function timelineStatus(events: MonitorEvent[]): TaskStatus | null {
  let status: TaskStatus | null = null;
  for (const event of events) {
    if (event.event === "session_created") status = "running";
    else if (event.event === "clarification_required") status = "awaiting_clarification";
    else if (event.event === "clarification_resolved") status = "running";
    else if (event.event === "task_result") status = "completed";
    else if (event.event === "task_cancelled") status = "cancelled";
    else if (event.event === "error") status = "error";
  }
  return status;
}

function snapshotAfterEvent(snapshot: TaskSnapshot | null, event: MonitorEvent): TaskSnapshot | null {
  if (!snapshot || snapshot.thread_id !== event.thread_id || snapshot.run_id !== event.run_id) return snapshot;
  const base = { ...snapshot, updated_at: event.timestamp };
  if (event.event === "intent_resolved") {
    return {
      ...base,
      resolved_query: event.data.resolved_query,
      resolved_intent: event.data.resolved_intent,
      mode: event.data.resolved_intent.mode,
      working_assumptions: event.data.resolved_intent.working_assumptions,
      applied_preferences: event.data.applied_preferences,
      task_overrides: event.data.task_overrides,
      constraint_relaxations: event.data.constraint_relaxations,
    };
  }
  if (event.event === "task_result") {
    const result = event.data;
    return {
      ...base,
      status: "completed",
      resolved_query: result.resolved_query ?? base.resolved_query,
      resolved_intent: result.resolved_intent ?? base.resolved_intent,
      mode: result.mode,
      working_assumptions: result.working_assumptions ?? base.working_assumptions,
      applied_preferences: result.applied_preferences ?? base.applied_preferences,
      task_overrides: result.task_overrides ?? base.task_overrides,
      constraint_relaxations: result.constraint_relaxations ?? base.constraint_relaxations,
      provider_coverage: result.providers,
      product_evidence: result.product_evidence ?? base.product_evidence,
      exchange_rate: result.exchange_rate,
      report_references: result.files,
      recall_provenance: result.recall_provenance ?? base.recall_provenance,
      result,
      error_code: null,
      error: null,
    };
  }
  if (event.event === "report_generated") {
    return {
      ...base,
      report_references: event.data.files,
      result: snapshot?.result
        ? { ...snapshot.result, files: event.data.files }
        : snapshot?.result ?? null,
    };
  }
  if (event.event === "task_cancelled") {
    return { ...base, status: "cancelled", result: null, clarification: null, error_code: null, error: null };
  }
  if (event.event === "error") {
    return {
      ...base,
      status: "error",
      result: null,
      clarification: null,
      error_code: event.data.code,
      error: event.message,
    };
  }
  if (event.event === "clarification_required") {
    return {
      ...base,
      status: "awaiting_clarification",
      result: null,
      clarification: {
        field: event.data.field,
        reason_code: event.data.reason_code,
        question: event.data.question,
      },
      error_code: null,
      error: null,
    };
  }
  if (event.event === "clarification_resolved") {
    return {
      ...base,
      status: "running",
      clarification: null,
      clarification_answers: {
        ...base.clarification_answers,
        [event.data.field]: event.data.resolved_value ?? event.data.response,
      },
      error_code: null,
      error: null,
    };
  }
  return base;
}

type Action =
  | { type: "service_checking" }
  | { type: "service_loaded"; health: HealthResponse; readiness: ReadinessResponse }
  | { type: "service_failure"; message: string }
  | { type: "starting"; query: string }
  | { type: "loading_snapshot"; threadId: string; query: string; knownStatus?: TaskStatus }
  | { type: "snapshot_load_failure"; message: string; serviceUnavailable: boolean }
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
        runId: null,
        events: [],
        result: null,
        snapshot: null,
        clarification: null,
        error: null,
        providerMode: "unverified",
        loadingSnapshot: false,
        loadError: null,
        snapshotFallbackStatus: null,
      };
    case "loading_snapshot":
      return {
        ...state,
        threadId: action.threadId,
        runId: null,
        query: action.query,
        status: action.knownStatus ?? "idle",
        connection: "idle",
        events: [],
        result: null,
        snapshot: null,
        clarification: null,
        error: null,
        providerMode: "unverified",
        loadingSnapshot: true,
        loadError: null,
        snapshotFallbackStatus: action.knownStatus ?? null,
      };
    case "snapshot_load_failure":
      return {
        ...state,
        status: state.snapshotFallbackStatus ?? state.status,
        connection: "disconnected",
        error: null,
        loadingSnapshot: false,
        loadError: action.message,
        ...(action.serviceUnavailable
          ? {
              health: null,
              readiness: null,
              serviceStatus: "unavailable" as const,
              serviceError: action.message,
            }
          : {}),
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
      if (state.threadId && action.event.thread_id !== state.threadId) return state;
      if (state.runId && action.event.run_id !== state.runId) return state;
      const events = mergeEvents(state.events, [action.event]);
      const terminal = terminalState(events);
      const result = timelineResult(events) ?? state.result;
      const nextStatus =
        terminal ??
        timelineStatus(events) ??
        (["completed", "cancelled", "error"].includes(state.status) ? state.status : "running");
      const clarification = timelineClarification(events);
      return {
        ...state,
        threadId: state.threadId ?? action.event.thread_id,
        runId: state.runId ?? action.event.run_id,
        events,
        status: nextStatus,
        result,
        snapshot: snapshotAfterEvent(state.snapshot, action.event),
        clarification,
        error:
          terminal === "error"
            ? timelineError(events) ?? "研究流程未能完成"
            : nextStatus === "awaiting_clarification"
              ? null
              : state.error,
        providerMode: result ? normalizeProviderMode(result.provider_mode) : state.providerMode,
      };
    }
    case "cancelled":
      return { ...state, status: "cancelled", connection: "idle", clarification: null, error: null };
    case "failure":
      return {
        ...state,
        status: "error",
        connection: "disconnected",
        error: action.message,
        loadingSnapshot: false,
        loadError: null,
        snapshotFallbackStatus: null,
      };
    case "snapshot": {
      const snapshot = action.snapshot;
      const preserveCurrentEvents =
        action.preserveEvents &&
        state.threadId === snapshot.thread_id &&
        state.runId === snapshot.run_id;
      const events = preserveCurrentEvents
        ? mergeEvents(snapshot.events ?? [], state.events)
        : mergeEvents(snapshot.events ?? []);
      const result = snapshot.result ?? timelineResult(events) ?? (preserveCurrentEvents ? state.result : null);
      const normalized = normalizeSnapshotStatus(snapshot.status, Boolean(result));
      const timeline = preserveCurrentEvents ? timelineStatus(events) : null;
      const status: TaskStatus = ["awaiting_clarification", "completed", "cancelled", "error"].includes(normalized)
        ? (normalized as TaskStatus)
        : timeline ?? normalized;
      return {
        ...state,
        threadId: snapshot.thread_id,
        runId: snapshot.run_id,
        query: snapshot.query || action.fallbackQuery || state.query,
        status,
        connection: status === "running" || status === "awaiting_clarification" ? state.connection : "idle",
        events,
        result,
        snapshot,
        clarification: snapshot.clarification ?? timelineClarification(events),
        providerMode: normalizeProviderMode(result?.provider_mode),
        error: snapshot.error ?? timelineError(events),
        loadingSnapshot: false,
        loadError: null,
        snapshotFallbackStatus: null,
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
  if (["awaiting_clarification", "awaiting-clarification"].includes(status)) return "awaiting_clarification";
  if (["running", "started", "pending"].includes(status)) return "running";
  return "idle";
}

export function parseSocketMessage(raw: string): MonitorEvent | TaskSnapshotMessage | null {
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (
      value.type === "task_snapshot" &&
      value.snapshot &&
      typeof value.snapshot === "object" &&
      typeof (value.snapshot as Record<string, unknown>).run_id === "string" &&
      value.timestamp
    ) {
      return value as unknown as TaskSnapshotMessage;
    }
    if (
      value.type !== "monitor_event" ||
      typeof value.event_id !== "string" ||
      typeof value.thread_id !== "string" ||
      typeof value.run_id !== "string" ||
      typeof value.sequence !== "number" ||
      typeof value.event !== "string" ||
      typeof value.timestamp !== "string"
    ) {
      return null;
    }
    return value as unknown as MonitorEvent;
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

interface SnapshotRecoveryOptions extends SnapshotSyncOptions {
  wait: () => Promise<void>;
}

export async function runSnapshotRecovery(
  options: SnapshotRecoveryOptions,
): Promise<TaskSnapshot | null> {
  while (options.isCurrent()) {
    try {
      const snapshot = await options.request(options.threadId, options.signal);
      if (!options.isCurrent()) return null;
      options.apply(snapshot, options.fallbackQuery);
      const status = normalizeSnapshotStatus(snapshot.status, Boolean(snapshot.result));
      if (status !== "running") {
        return snapshot;
      }
    } catch {
      if (!options.isCurrent()) return null;
    }
    await options.wait();
  }
  return null;
}

export function useShoppingAgent() {
  const [state, dispatch] = useReducer(agentReducer, initialAgentState);
  const stateRef = useRef(state);
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
  const clarificationRequestRef = useRef<AbortController | null>(null);
  const serviceRequestRef = useRef<AbortController | null>(null);
  const snapshotSyncRequestRef = useRef<AbortController | null>(null);
  const taskIntentRef = useRef<{ generation: number; threadId: string | null }>({ generation: 0, threadId: null });

  stateRef.current = state;

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
    snapshotSyncRequestRef.current?.abort();
    snapshotSyncRequestRef.current = null;
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

  const recoverSnapshot = useCallback(async (threadId: string, fallbackQuery?: string) => {
    snapshotSyncRequestRef.current?.abort();
    const controller = new AbortController();
    snapshotSyncRequestRef.current = controller;
    const intent = taskIntentRef.current;
    const isCurrent = () =>
      snapshotSyncRequestRef.current === controller &&
      !disposedRef.current &&
      taskIntentRef.current.generation === intent.generation &&
      taskIntentRef.current.threadId === threadId;
    try {
      const terminal = await runSnapshotRecovery({
        threadId,
        fallbackQuery,
        signal: controller.signal,
        request: (requestedThreadId, signal) => api.taskSnapshot(requestedThreadId, { signal }),
        isCurrent,
        apply: (snapshot, query) =>
          dispatch({ type: "snapshot", snapshot, fallbackQuery: query, preserveEvents: true }),
        wait: () =>
          new Promise<void>((resolve) => {
            let timeout: number | null = null;
            const finish = () => {
              if (timeout != null) window.clearTimeout(timeout);
              controller.signal.removeEventListener("abort", finish);
              resolve();
            };
            timeout = window.setTimeout(finish, 2_000);
            controller.signal.addEventListener("abort", finish, { once: true });
            if (controller.signal.aborted) finish();
          }),
      });
      if (terminal && isCurrent()) {
        const status = normalizeSnapshotStatus(terminal.status, Boolean(terminal.result));
        terminalRef.current = ["completed", "cancelled", "error"].includes(status);
      }
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
        const payload = parseSocketMessage(message.data);
        if (!payload) return;
        if (payload.type === "task_snapshot") {
          dispatch({ type: "snapshot", snapshot: payload.snapshot, fallbackQuery, preserveEvents: true });
          const snapshotStatus = normalizeSnapshotStatus(payload.snapshot.status, Boolean(payload.snapshot.result));
          if (["completed", "cancelled", "error"].includes(snapshotStatus)) {
            terminalRef.current = true;
            if (heartbeatTimerRef.current != null) window.clearInterval(heartbeatTimerRef.current);
            heartbeatTimerRef.current = null;
            window.setTimeout(() => {
              if (generation === socketGenerationRef.current) socket.close(1000, "task complete");
            }, 80);
          }
          return;
        }
        dispatch({ type: "event", event: payload });
        if (TERMINAL_EVENTS.has(payload.event)) {
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
          void recoverSnapshot(threadId, fallbackQuery);
          return;
        }
        reconnectCountRef.current += 1;
        dispatch({ type: "connection", connection: "reconnecting" });
        const delay = Math.min(800 * 2 ** (reconnectCountRef.current - 1), 8_000);
        reconnectTimerRef.current = window.setTimeout(() => openSocket(threadId, fallbackQuery), delay);
      };
    },
    [recoverSnapshot],
  );

  const startTask = useCallback(
    async (payload: Omit<TaskRequest, "thread_id"> & { thread_id?: string | null }) => {
      if (!state.readiness?.task_ready) return null;
      loadRequestRef.current?.abort();
      loadRequestRef.current = null;
      cancelRequestRef.current?.abort();
      cancelRequestRef.current = null;
      clarificationRequestRef.current?.abort();
      clarificationRequestRef.current = null;
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
        await syncSnapshot(state.threadId, state.query);
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

  const respondToClarification = useCallback(
    async (response: string): Promise<{ ok: boolean; message?: string }> => {
      const current = stateRef.current;
      if (!current.threadId || current.status !== "awaiting_clarification") {
        return { ok: false, message: "这次研究已经不在等待澄清" };
      }
      clarificationRequestRef.current?.abort();
      const controller = new AbortController();
      clarificationRequestRef.current = controller;
      try {
        await api.clarifyTask(current.threadId, response, { signal: controller.signal });
        if (clarificationRequestRef.current !== controller || disposedRef.current) return { ok: false };
        if (stateRef.current.connection !== "connected") connect(current.threadId, current.query);
        await syncSnapshot(current.threadId, current.query);
        return { ok: true };
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return { ok: false };
        return { ok: false, message: error instanceof Error ? error.message : "提交澄清回答失败" };
      } finally {
        if (clarificationRequestRef.current === controller) clarificationRequestRef.current = null;
      }
    },
    [connect, syncSnapshot],
  );

  const loadThread = useCallback(
    async (threadId: string, fallbackQuery: string, knownStatus?: TaskStatus) => {
      startRequestRef.current?.abort();
      loadRequestRef.current?.abort();
      cancelRequestRef.current?.abort();
      clarificationRequestRef.current?.abort();
      replaceTaskIntent(threadId);
      closeSocket();
      terminalRef.current = false;
      dispatch({ type: "loading_snapshot", threadId, query: fallbackQuery, knownStatus });
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
        const snapshotStatus = normalizeSnapshotStatus(snapshot.status, Boolean(snapshot.result));
        if (snapshotStatus === "running" || snapshotStatus === "awaiting_clarification") {
          connect(threadId, fallbackQuery);
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (
          loadRequestRef.current !== controller ||
          disposedRef.current ||
          taskIntentRef.current.generation !== intentGeneration ||
          taskIntentRef.current.threadId !== threadId
        ) {
          return;
        }
        const message = error instanceof Error ? error.message : "无法读取这次研究";
        const serviceUnavailable =
          error instanceof ApiError && (error.status === 0 || error.status === 408 || error.status >= 500);
        dispatch({ type: "snapshot_load_failure", message, serviceUnavailable });
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
    clarificationRequestRef.current?.abort();
    clarificationRequestRef.current = null;
    replaceTaskIntent(null);
    closeSocket();
    terminalRef.current = true;
    dispatch({ type: "reset" });
  }, [closeSocket, replaceTaskIntent]);

  const clearDeletedThread = useCallback(
    (threadId: string) => {
      if (taskIntentRef.current.threadId !== threadId) return false;
      reset();
      return true;
    },
    [reset],
  );

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
      clarificationRequestRef.current?.abort();
      snapshotSyncRequestRef.current?.abort();
      closeSocket();
    };
  }, [closeSocket, refreshReadiness]);

  return {
    state,
    startTask,
    cancelTask,
    respondToClarification,
    loadThread,
    reset,
    clearDeletedThread,
    reconnect,
    refreshReadiness,
  };
}
