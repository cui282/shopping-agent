import { describe, expect, it } from "vitest";
import { agentReducer, initialAgentState, runSnapshotSync } from "./useShoppingAgent";
import type { MonitorEvent, TaskResultData, TaskSnapshot } from "../types/api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("agentReducer", () => {
  it("folds a buffered task result into a completed snapshot", () => {
    const result: TaskResultData = {
      thread_id: "thread-42",
      final_answer: "已找到两款候选",
      recommendations: [],
      comparison: [],
      files: [],
      provider_mode: "sandbox",
      providers: {},
      calculation_notice: "运税为估算值",
    };
    const event: MonitorEvent = {
      type: "monitor_event",
      event: "task_result",
      message: "推荐已生成",
      data: result as unknown as Record<string, unknown>,
      timestamp: "2026-07-30T12:00:00Z",
    };

    const state = agentReducer(initialAgentState, { type: "event", event });
    expect(state.status).toBe("completed");
    expect(state.result?.thread_id).toBe("thread-42");
    expect(state.events).toHaveLength(1);
  });

  it("keeps a loaded task running when a snapshot has no result", () => {
    const state = agentReducer(initialAgentState, {
      type: "snapshot",
      snapshot: { thread_id: "thread-live", status: "running", query: "test" },
    });
    expect(state.status).toBe("running");
    expect(state.query).toBe("test");
  });

  it("deduplicates a replayed monitor event", () => {
    const event: MonitorEvent = {
      type: "monitor_event",
      event: "tool_start",
      message: "正在调用 item_search 工具",
      data: { tool_name: "item_search" },
      timestamp: "2026-07-30T12:00:01Z",
    };
    const once = agentReducer(initialAgentState, { type: "event", event });
    const replayed = agentReducer(once, { type: "event", event });
    expect(replayed.events).toHaveLength(1);
  });

  it("clears events when loading a snapshot from another task", () => {
    const event: MonitorEvent = {
      type: "monitor_event",
      event: "tool_start",
      message: "正在检索",
      data: { tool_name: "item_search" },
      timestamp: "2026-07-30T12:00:01Z",
    };
    const current = agentReducer(initialAgentState, { type: "event", event });
    const loaded = agentReducer(current, {
      type: "snapshot",
      snapshot: { thread_id: "thread-other", status: "running", query: "另一项研究" },
    });
    expect(loaded.events).toEqual([]);
  });

  it("preserves events when synchronizing the same task after a disconnect", () => {
    const event: MonitorEvent = {
      type: "monitor_event",
      event: "tool_end",
      message: "检索完成",
      data: { tool_name: "item_search" },
      timestamp: "2026-07-30T12:00:02Z",
    };
    const current = agentReducer(initialAgentState, { type: "event", event });
    const synced = agentReducer(current, {
      type: "snapshot",
      snapshot: { thread_id: "thread-current", status: "running", query: "当前研究" },
      preserveEvents: true,
    });
    expect(synced.events).toEqual([event]);
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
    response.resolve({ thread_id: "thread-old", status: "completed", query: "旧研究" });
    await pending;

    expect(applied).toEqual([]);
  });
});
