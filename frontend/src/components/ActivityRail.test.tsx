// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { initialAgentState } from "../hooks/useShoppingAgent";
import type { MonitorEvent } from "../types/api";
import ActivityRail from "./ActivityRail";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function event(
  sequence: number,
  name: MonitorEvent["event"],
  data: Record<string, unknown>,
  runId = "1".repeat(32),
): MonitorEvent {
  return {
    type: "monitor_event",
    event_id: `evt-${String(sequence).padStart(32, "0")}`,
    thread_id: "thread-activity",
    run_id: runId,
    sequence,
    event: name,
    message: `${name} ${sequence}`,
    data,
    timestamp: `2026-07-30T12:00:${String(sequence).padStart(2, "0")}Z`,
  } as MonitorEvent;
}

describe("ActivityRail timeline", () => {
  it("renders the complete keyboard-scrollable history with fork and tool provenance", async () => {
    vi.spyOn(api, "preferences").mockResolvedValue({ preferences: {} });
    const query = "预算 1200 元找轻便降噪耳机，不要皮革";
    const events = [
      event(1, "fork", {
        sub_thread_id: "sub-00000001",
        platform: "amazon",
        demand: { platform: "amazon", query },
      }),
      ...Array.from({ length: 19 }, (_, index) =>
        event(index + 2, "assistant_call", { step: "acting" }),
      ),
      event(21, "tool_end", {
        tool_name: "item_search",
        duration_ms: 240,
        outcome: "degraded",
        source: "fixture",
        provider: "sandbox-fixture",
        status: "degraded",
        fallback_reason: "SANDBOX_MODE is enabled",
      }),
    ];

    render(
      <ActivityRail
        state={{
          ...initialAgentState,
          threadId: "thread-activity",
          status: "running",
          events,
        }}
        userId="activity-user"
        preferenceStore="memory"
        onClose={vi.fn()}
      />,
    );

    const timeline = screen.getByRole("list", { name: "完整研究活动历史" });
    expect(within(timeline).getAllByRole("listitem")).toHaveLength(21);
    expect(within(timeline).getByText("并行检索 · Amazon")).toBeTruthy();
    expect(within(timeline).getByText(`需求：${query}`)).toBeTruthy();
    const degradedDetail = within(timeline).getByText("240 毫秒 · 沙盒样本 · 已降级 · 降级");
    expect(degradedDetail.closest("li")?.dataset.outcome).toBe("degraded");
    expect(within(timeline).getByText(/已显式启用沙盒模式/)).toBeTruthy();

    timeline.focus();
    expect(document.activeElement).toBe(timeline);
    expect(await screen.findByText("还没有记住的偏好。")).toBeTruthy();
  });

  it("restores automatic following when the selected run changes", () => {
    vi.spyOn(api, "preferences").mockResolvedValue({ preferences: {} });
    const firstEvents = Array.from({ length: 3 }, (_, index) =>
      event(index + 1, "assistant_call", { step: "acting" }),
    );
    const { rerender } = render(
      <ActivityRail
        state={{
          ...initialAgentState,
          threadId: "thread-reused",
          runId: "1".repeat(32),
          status: "running",
          events: firstEvents,
        }}
        userId="activity-user"
        preferenceStore="memory"
        onClose={vi.fn()}
      />,
    );
    const timeline = screen.getByRole("list", { name: "完整研究活动历史" });
    Object.defineProperties(timeline, {
      scrollHeight: { configurable: true, value: 600 },
      clientHeight: { configurable: true, value: 100 },
    });
    timeline.scrollTop = 100;
    fireEvent.scroll(timeline);

    rerender(
      <ActivityRail
        state={{
          ...initialAgentState,
          threadId: "thread-reused",
          runId: "2".repeat(32),
          status: "running",
          events: [
            event(
              1,
              "session_created",
              { thread_id: "thread-reused", reference_images: [] },
              "2".repeat(32),
            ),
          ],
        }}
        userId="activity-user"
        preferenceStore="memory"
        onClose={vi.fn()}
      />,
    );

    expect(timeline.scrollTop).toBe(600);
  });
});
