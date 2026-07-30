import { describe, expect, it } from "vitest";
import { socketCloseDecision } from "./socketLifecycle";

describe("socketCloseDecision", () => {
  it("ignores a socket closed intentionally after its generation was replaced", () => {
    expect(
      socketCloseDecision({
        generation: 2,
        currentGeneration: 3,
        disposed: false,
        terminal: false,
        reconnectCount: 0,
        maxReconnects: 5,
      }),
    ).toBe("ignore");
  });

  it("syncs the snapshot after the reconnect budget is exhausted", () => {
    expect(
      socketCloseDecision({
        generation: 3,
        currentGeneration: 3,
        disposed: false,
        terminal: false,
        reconnectCount: 5,
        maxReconnects: 5,
      }),
    ).toBe("sync");
  });
});
