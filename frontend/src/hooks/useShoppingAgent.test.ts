import { describe, expect, it } from "vitest";
import {
  agentReducer,
  initialAgentState,
  parseSocketMessage,
  runSnapshotRecovery,
  runSnapshotSync,
} from "./useShoppingAgent";
import type {
  MonitorEvent,
  RecallChannelReport,
  TaskResultData,
  TaskSnapshot,
} from "../types/api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function timelineEvent(
  sequence: number,
  event: MonitorEvent["event"],
  data: Record<string, unknown> = {},
): MonitorEvent {
  return {
    type: "monitor_event",
    event_id: `evt-${String(sequence).padStart(32, "0")}`,
    thread_id: "thread-timeline",
    run_id: "1".repeat(32),
    sequence,
    event,
    message: `${event} ${sequence}`,
    data,
    timestamp: `2026-07-30T12:00:${String(sequence).padStart(2, "0")}Z`,
  } as MonitorEvent;
}

function taskSnapshot(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    snapshot_id: "thread-live",
    thread_id: "thread-live",
    run_id: "1".repeat(32),
    generation: 0,
    status: "running",
    query: "test",
    user_id: "test-user",
    data_mode: "live",
    created_at: "2026-07-30T12:00:00Z",
    updated_at: "2026-07-30T12:00:00Z",
    lineage: null,
    resolved_query: null,
    resolved_intent: null,
    mode: null,
    working_assumptions: [],
    applied_preferences: {
      material_preferences: [],
      style_preferences: [],
      soft_preferences: [],
      avoid: [],
    },
    task_overrides: [],
    constraint_relaxations: [],
    provider_coverage: {},
    product_evidence: [],
    exchange_rate: null,
    report_references: [],
    events: [],
    result: null,
    clarification: null,
    clarification_answers: {},
    error_code: null,
    error: null,
    ...overrides,
  };
}

function fallbackRecallChannel(channel: RecallChannelReport["channel"]): RecallChannelReport {
  return {
    channel,
    configured: false,
    state: "unavailable",
    reason_code: "not_configured",
    reason: "channel is not configured; deterministic fallback remains active",
    participated: false,
  };
}

describe("agentReducer", () => {
  it("folds the durable report generation event without changing task status", () => {
    const initial = agentReducer(initialAgentState, {
      type: "snapshot",
      snapshot: taskSnapshot(),
    });
    const files = [
      {
        file_id: "thread-live:pdf",
        format: "pdf",
        name: "shopping-report.pdf",
        url: "/api/files/thread-live/shopping-report.pdf",
        content_type: "application/pdf",
      },
    ];
    const next = agentReducer(initial, {
      type: "event",
      event: {
        ...timelineEvent(1, "report_generated", {
          snapshot_id: "thread-live",
          snapshot_effective_at: "2026-07-30T12:00:00Z",
          files,
          data_mode: "live",
        }),
        thread_id: "thread-live",
      },
    });

    expect(next.status).toBe("running");
    expect(next.snapshot?.report_references).toEqual(files);
  });

  it("folds a buffered task result into a completed snapshot", () => {
    const result: TaskResultData = {
      thread_id: "thread-42",
      final_answer: "已找到两款候选",
      resolved_query: null,
      resolved_intent: null,
      applied_preferences: {
        material_preferences: [],
        style_preferences: [],
        soft_preferences: [],
        avoid: [],
      },
      task_overrides: [],
      constraint_relaxations: [],
      product_evidence: [],
      mode: "product_research",
      recommendations: [],
      comparison: [],
      matching_offers: [],
      alternative_candidates: [],
      files: [],
      provider_mode: "sandbox",
      providers: {},
      calculation_notice: "运税为估算值",
      exchange_rate: {
        base_currency: "CNY",
        source: "reference-table",
        effective_date: "2026-01-01",
        calculation_basis: "original_amount * rate_to_cny",
      },
      calculation_exclusions: [],
      ranking_profile: {
        priority_order: ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
        explicit: false,
      },
      data_mode: "sandbox",
      result_kind: "sandbox",
      unavailable_marketplaces: [],
      recall_provenance: {
        mode: "deterministic_fallback",
        channels: {
          opensearch: fallbackRecallChannel("opensearch"),
          query_tower: fallbackRecallChannel("query_tower"),
          item_tower: fallbackRecallChannel("item_tower"),
          faiss: fallbackRecallChannel("faiss"),
        },
        participating_channels: [],
        fallback_reason: "optional_recall_unavailable",
        input_candidate_count: 0,
        selected_candidate_count: 0,
      },
    };
    const event = timelineEvent(1, "task_result", result as unknown as Record<string, unknown>);

    const loaded = agentReducer(initialAgentState, {
      type: "snapshot",
      snapshot: taskSnapshot({ thread_id: "thread-timeline" }),
    });
    const state = agentReducer(loaded, { type: "event", event });
    expect(state.status).toBe("completed");
    expect(state.result?.thread_id).toBe("thread-42");
    expect(state.snapshot?.recall_provenance?.mode).toBe("deterministic_fallback");
    expect(state.events).toHaveLength(1);
  });

  it("keeps a loaded task running when a snapshot has no result", () => {
    const state = agentReducer(initialAgentState, {
      type: "snapshot",
      snapshot: taskSnapshot(),
    });
    expect(state.status).toBe("running");
    expect(state.query).toBe("test");
  });

  it("folds resolved intent fields into the authoritative snapshot", () => {
    const loaded = agentReducer(initialAgentState, {
      type: "snapshot",
      snapshot: taskSnapshot({ thread_id: "thread-timeline" }),
    });
    const event = timelineEvent(1, "intent_resolved", {
      resolved_query: "预算 1200 元找耳机",
      resolved_intent: {
        mode: "product_research",
        working_assumptions: [],
      },
      applied_preferences: {
        material_preferences: [],
        style_preferences: ["简约"],
        soft_preferences: [],
        avoid: [],
      },
      task_overrides: [],
      constraint_relaxations: [],
      data_mode: "live",
    });

    const next = agentReducer(loaded, { type: "event", event });

    expect(next.snapshot).toMatchObject({
      resolved_query: "预算 1200 元找耳机",
      mode: "product_research",
      applied_preferences: { style_preferences: ["简约"] },
    });
  });

  it("enters and leaves the durable clarification state through ordered events", () => {
    const required = timelineEvent(1, "clarification_required", {
      field: "mode",
      reason_code: "mode_ambiguous",
      question: "请选择比较模式",
      data_mode: "sandbox",
    });
    const waiting = agentReducer(initialAgentState, { type: "event", event: required });

    expect(waiting.status).toBe("awaiting_clarification");
    expect(waiting.clarification).toEqual({
      field: "mode",
      reason_code: "mode_ambiguous",
      question: "请选择比较模式",
    });

    const resolved = timelineEvent(2, "clarification_resolved", {
      field: "mode",
      reason_code: "mode_ambiguous",
      response: "比较不同产品",
      resolved_value: "product_research",
      data_mode: "sandbox",
    });
    const resumed = agentReducer(waiting, { type: "event", event: resolved });
    expect(resumed.status).toBe("running");
    expect(resumed.clarification).toBeNull();
  });

  it("restores an awaiting question from a durable snapshot", () => {
    const snapshot = taskSnapshot({
      status: "awaiting_clarification",
      clarification: {
        field: "destination",
        reason_code: "destination_ambiguous",
        question: "请确认收货地",
      },
    });
    const restored = agentReducer(initialAgentState, { type: "snapshot", snapshot });
    expect(restored.status).toBe("awaiting_clarification");
    expect(restored.clarification?.field).toBe("destination");
  });

  it("deduplicates a replayed monitor event", () => {
    const event = timelineEvent(1, "tool_start", { tool_name: "item_search" });
    const once = agentReducer(initialAgentState, { type: "event", event });
    const replayed = agentReducer(once, { type: "event", event });
    expect(replayed.events).toHaveLength(1);
  });

  it("clears events when loading a snapshot from another task", () => {
    const event = timelineEvent(1, "tool_start", { tool_name: "item_search" });
    const current = agentReducer(initialAgentState, { type: "event", event });
    const loaded = agentReducer(current, {
      type: "snapshot",
      snapshot: taskSnapshot({ thread_id: "thread-other", query: "另一项研究" }),
    });
    expect(loaded.events).toEqual([]);
  });

  it("preserves events when synchronizing the same task after a disconnect", () => {
    const event = timelineEvent(2, "tool_end", { tool_name: "item_search" });
    const current = agentReducer(initialAgentState, { type: "event", event });
    const synced = agentReducer(current, {
      type: "snapshot",
      snapshot: taskSnapshot({
        thread_id: "thread-timeline",
        run_id: "1".repeat(32),
        query: "当前研究",
      }),
      preserveEvents: true,
    });
    expect(synced.events).toEqual([event]);
  });

  it("orders events by sequence and deduplicates stable event ids", () => {
    const second = timelineEvent(2, "tool_end", { tool_name: "planner" });
    const first = timelineEvent(1, "tool_start", { tool_name: "planner" });
    const replayedSecond = { ...second, timestamp: "2026-07-30T12:59:59Z" };

    const afterSecond = agentReducer(initialAgentState, { type: "event", event: second });
    const afterFirst = agentReducer(afterSecond, { type: "event", event: first });
    const replayed = agentReducer(afterFirst, { type: "event", event: replayedSecond });

    expect(replayed.events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(replayed.events).toHaveLength(2);
  });

  it.each([
    ["task_result", "completed"],
    ["task_cancelled", "cancelled"],
    ["error", "error"],
  ] as const)("does not roll %s back when an older non-terminal event arrives", (terminalName, status) => {
    const terminalData =
      terminalName === "task_result"
        ? {
            thread_id: "thread-timeline",
            final_answer: "done",
            mode: "product_research",
            recommendations: [],
            comparison: [],
            matching_offers: [],
            alternative_candidates: [],
            files: [],
            provider_mode: "sandbox",
            providers: {},
            calculation_notice: "test",
            exchange_rate: {
              base_currency: "CNY",
              source: "reference-table",
              effective_date: "2026-01-01",
              calculation_basis: "original_amount * rate_to_cny",
            },
            calculation_exclusions: [],
            ranking_profile: {
              priority_order: ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
              explicit: false,
            },
            data_mode: "sandbox",
            result_kind: "sandbox",
            unavailable_marketplaces: [],
          }
        : {};
    const terminal = timelineEvent(3, terminalName, terminalData);
    const completed = agentReducer(initialAgentState, { type: "event", event: terminal });
    const withLateEvent = agentReducer(completed, {
      type: "event",
      event: timelineEvent(2, "tool_end", { tool_name: "planner" }),
    });

    expect(withLateEvent.status).toBe(status);
    expect(withLateEvent.events.map((event) => event.sequence)).toEqual([2, 3]);
  });

  it("merges a stale disconnect snapshot without dropping newer live events", () => {
    const live = timelineEvent(2, "tool_end", { tool_name: "planner" });
    const current = agentReducer(initialAgentState, { type: "event", event: live });
    const synced = agentReducer(current, {
      type: "snapshot",
      snapshot: taskSnapshot({
        thread_id: "thread-timeline",
        query: "当前研究",
        events: [timelineEvent(1, "tool_start", { tool_name: "planner" })],
      }),
      preserveEvents: true,
    });

    expect(synced.events.map((event) => event.sequence)).toEqual([1, 2]);
  });

  it("replaces old events when a same-thread snapshot starts a different run", () => {
    const oldEvent = timelineEvent(1, "tool_start", { tool_name: "planner" });
    const current = {
      ...agentReducer(initialAgentState, { type: "event", event: oldEvent }),
      threadId: "thread-timeline",
    };
    const newEvent = {
      ...timelineEvent(1, "session_created", { thread_id: "thread-timeline", reference_images: [] }),
      run_id: "2".repeat(32),
    };
    const replaced = agentReducer(current, {
      type: "snapshot",
      snapshot: taskSnapshot({
        thread_id: "thread-timeline",
        run_id: "2".repeat(32),
        query: "替换后的研究",
        events: [newEvent],
      }),
      preserveEvents: true,
    });

    expect(replaced.runId).toBe("2".repeat(32));
    expect(replaced.events).toEqual([newEvent]);
  });

  it("ignores a live event from a superseded run", () => {
    const current = agentReducer(initialAgentState, {
      type: "snapshot",
      snapshot: taskSnapshot({ run_id: "2".repeat(32) }),
    });
    const stale = timelineEvent(1, "tool_start", { tool_name: "planner" });

    expect(agentReducer(current, { type: "event", event: stale })).toBe(current);
  });
});

describe("snapshot synchronization races", () => {
  it.each([
    ["reset", null],
    ["start", null],
    ["load", "thread-new"],
  ])("does not apply an old snapshot after %s changes the task intent", async (_transition, nextThreadId) => {
    const response = deferred<TaskSnapshot>();
    let currentIntent = { generation: 1, threadId: "thread-old" as string | null };
    const capturedIntent = currentIntent;
    const applied: TaskSnapshot[] = [];
    const pending = runSnapshotSync({
      threadId: "thread-old",
      fallbackQuery: "旧研究",
      request: () => response.promise,
      isCurrent: () =>
        currentIntent.generation === capturedIntent.generation && currentIntent.threadId === capturedIntent.threadId,
      apply: (snapshot) => applied.push(snapshot),
    });

    currentIntent = {
      generation: currentIntent.generation + 1,
      threadId: nextThreadId,
    };
    response.resolve(taskSnapshot({ thread_id: "thread-old", status: "completed", query: "旧研究" }));
    await pending;

    expect(applied).toEqual([]);
  });

  it.each(["completed", "cancelled", "error"] as const)(
    "keeps polling after reconnect exhaustion until a %s snapshot arrives",
    async (terminalStatus) => {
      const responses = [
        taskSnapshot({ status: "running" }),
        taskSnapshot({
          status: terminalStatus,
          error: terminalStatus === "error" ? "研究失败" : null,
        }),
      ];
      const applied: TaskSnapshot[] = [];
      let waits = 0;

      const terminal = await runSnapshotRecovery({
        threadId: "thread-live",
        request: async () => responses.shift()!,
        isCurrent: () => true,
        apply: (snapshot) => applied.push(snapshot),
        wait: async () => {
          waits += 1;
        },
      });

      expect(applied.map((snapshot) => snapshot.status)).toEqual(["running", terminalStatus]);
      expect(waits).toBe(1);
      expect(terminal?.status).toBe(terminalStatus);
    },
  );

  it("stops snapshot recovery at an awaiting clarification without treating it as terminal", async () => {
    const responses = [
      taskSnapshot({ status: "running" }),
      taskSnapshot({
        status: "awaiting_clarification",
        clarification: {
          field: "mode",
          reason_code: "mode_ambiguous",
          question: "请选择比较模式",
        },
      }),
    ];
    let waits = 0;

    const restored = await runSnapshotRecovery({
      threadId: "thread-live",
      request: async () => responses.shift()!,
      isCurrent: () => true,
      apply: () => undefined,
      wait: async () => {
        waits += 1;
      },
    });

    expect(restored?.status).toBe("awaiting_clarification");
    expect(waits).toBe(1);
  });
});

describe("WebSocket protocol parsing", () => {
  it("accepts a durable task snapshot and rejects monitor events without stable ordering fields", () => {
    const snapshot = taskSnapshot({
      status: "cancelled",
      events: [timelineEvent(1, "task_cancelled", { thread_id: "thread-live" })],
    });
    const parsed = parseSocketMessage(
      JSON.stringify({
        type: "task_snapshot",
        snapshot,
        timestamp: "2026-07-30T12:00:01Z",
      }),
    );

    expect(parsed).toMatchObject({ type: "task_snapshot", snapshot: { status: "cancelled" } });
    expect(
      parseSocketMessage(
        JSON.stringify({
          type: "monitor_event",
          event: "error",
          message: "missing ordering fields",
          data: {},
          timestamp: "2026-07-30T12:00:01Z",
        }),
      ),
    ).toBeNull();
  });
});
