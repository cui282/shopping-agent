import { describe, expect, it } from "vitest";
import type { SessionHistoryItem } from "../types/api";
import { withoutSession } from "./useSessionHistory";

describe("session history", () => {
  it("removes only the requested research session", () => {
    const history: SessionHistoryItem[] = [
      { threadId: "one", query: "找手机", status: "completed", createdAt: "2026-07-30T08:00:00Z" },
      { threadId: "two", query: "找耳机", status: "completed", createdAt: "2026-07-30T09:00:00Z" },
    ];

    expect(withoutSession(history, "one")).toEqual([history[1]]);
  });
});
