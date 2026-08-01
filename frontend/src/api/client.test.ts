import { afterEach, describe, expect, it, vi } from "vitest";
import { api, requestJson, resolveApiUrl, safeExternalUrl } from "./client";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("URL handling", () => {
  it("allows only absolute HTTP(S) product links", () => {
    expect(safeExternalUrl("https://example.com/item?id=1")).toBe("https://example.com/item?id=1");
    expect(safeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("/relative-item")).toBeNull();
    expect(safeExternalUrl("https://user:password@example.com/item")).toBeNull();
  });

  it("resolves generated reports against the API origin", () => {
    expect(resolveApiUrl("/api/files/thread-1/report.md", "https://api.example.com/backend")).toBe(
      "https://api.example.com/api/files/thread-1/report.md",
    );
    expect(resolveApiUrl("data:text/plain,unsafe", "https://api.example.com")).toBeNull();
  });
});

describe("request lifecycle", () => {
  it("submits a legal 4000-character shopping query to the asynchronous task endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "started", thread_id: "thread-long-query" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const query = "商".repeat(4000);

    await api.startTask({ query, thread_id: null, user_id: "browser-user", upload_ids: [] });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/task$/),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query, thread_id: null, user_id: "browser-user", upload_ids: [] }),
      }),
    );
  });

  it("deletes a stored research task", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "deleted", thread_id: "thread-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.deleteTask("thread-1");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/task\/thread-1$/),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("propagates caller cancellation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
      ),
    );
    const controller = new AbortController();
    const request = api.health({ signal: controller.signal });
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });

  it("turns a deadline abort into an actionable timeout error", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
      ),
    );
    const request = requestJson("/slow", { timeoutMs: 50 });
    const assertion = expect(request).rejects.toMatchObject({ status: 408 });
    await vi.advanceTimersByTimeAsync(51);
    await assertion;
  });
});
