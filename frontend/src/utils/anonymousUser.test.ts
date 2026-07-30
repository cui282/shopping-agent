import { describe, expect, it } from "vitest";
import { createAnonymousUserId, getAnonymousUserId } from "./anonymousUser";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

describe("anonymous user identity", () => {
  it("creates a backend-safe anonymous id", () => {
    expect(createAnonymousUserId(() => "id.with spaces/unsafe")).toBe("anon-idwithspacesunsafe");
  });

  it("persists and reuses the id in browser storage", () => {
    const storage = memoryStorage();
    const first = getAnonymousUserId(storage, () => "fixed-id");
    const second = getAnonymousUserId(storage, () => "different-id");
    expect(first).toBe("anon-fixed-id");
    expect(second).toBe(first);
  });
});
