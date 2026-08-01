import type {
  HealthResponse,
  MemoryCommand,
  PreferenceDeleteResponse,
  PreferencesResponse,
  ReadinessResponse,
  TaskRequest,
  TaskSnapshot,
  TaskStartResponse,
  UploadResponse,
} from "../types/api";

const apiBase = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const DEFAULT_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number;
}

function errorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const value = body as { detail?: unknown; message?: unknown };
  if (typeof value.message === "string") return value.message;
  if (typeof value.detail === "string") return value.detail;
  if (value.detail && typeof value.detail === "object") {
    const detail = value.detail as { message?: unknown };
    if (typeof detail.message === "string") return detail.message;
  }
  return fallback;
}

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal: callerSignal, ...init } = options;
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(`${apiBase}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...init.headers,
      },
    });

    if (!response.ok) {
      let message = `请求失败（${response.status}）`;
      try {
        message = errorMessage(await response.json(), message);
      } catch {
        // The status code remains actionable when an upstream returns non-JSON.
      }
      throw new ApiError(message, response.status);
    }

    return (await response.json()) as T;
  } catch (error) {
    if (timedOut) throw new ApiError("请求超时，请检查网络后重试", 408);
    if (callerSignal?.aborted) throw new DOMException("请求已取消", "AbortError");
    if (error instanceof ApiError || (error instanceof DOMException && error.name === "AbortError")) throw error;
    if (error instanceof TypeError) throw new ApiError("无法连接购物研究服务", 0);
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
}

interface RequestControl {
  signal?: AbortSignal;
}

export const api = {
  health: (control: RequestControl = {}) => requestJson<HealthResponse>("/api/health", control),
  readiness: (control: RequestControl = {}) => requestJson<ReadinessResponse>("/api/readiness", control),
  startTask: (payload: TaskRequest, control: RequestControl = {}) =>
    requestJson<TaskStartResponse>("/api/task", {
      method: "POST",
      body: JSON.stringify(payload),
      ...control,
    }),
  taskSnapshot: (threadId: string, control: RequestControl = {}) =>
    requestJson<TaskSnapshot>(`/api/task/${encodeURIComponent(threadId)}`, control),
  cancelTask: (threadId: string, control: RequestControl = {}) =>
    requestJson<{ status: string; thread_id?: string }>(`/api/task/${encodeURIComponent(threadId)}/cancel`, {
      method: "POST",
      ...control,
    }),
  deleteTask: (threadId: string, control: RequestControl = {}) =>
    requestJson<{ status: "deleted"; thread_id: string }>(`/api/task/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
      ...control,
    }),
  upload: async (file: File, control: RequestControl = {}) => {
    const form = new FormData();
    form.append("file", file);
    return requestJson<UploadResponse>("/api/upload", { method: "POST", body: form, timeoutMs: 30_000, ...control });
  },
  preferences: (userId: string, control: RequestControl = {}) =>
    requestJson<PreferencesResponse>(`/api/preferences/${encodeURIComponent(userId)}`, control),
  updatePreferences: (userId: string, command: MemoryCommand, control: RequestControl = {}) =>
    requestJson<PreferencesResponse>(`/api/preferences/${encodeURIComponent(userId)}`, {
      method: "PUT",
      body: JSON.stringify(command),
      ...control,
    }),
  clearPreferences: (userId: string, control: RequestControl = {}) =>
    requestJson<PreferenceDeleteResponse>(`/api/preferences/${encodeURIComponent(userId)}`, {
      method: "DELETE",
      ...control,
    }),
};

function browserOrigin(): string {
  return typeof window === "undefined" ? "http://localhost" : window.location.origin;
}

export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const safeProtocol = url.protocol === "http:" || url.protocol === "https:";
    return safeProtocol && url.hostname && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

export function resolveApiUrl(value: string | null | undefined, baseUrl = apiBase || browserOrigin()): string | null {
  if (!value) return null;
  try {
    const base = new URL(baseUrl, browserOrigin());
    const url = new URL(value, base);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function buildWebSocketUrl(threadId: string): string {
  const configured = (import.meta.env.VITE_WS_BASE_URL as string | undefined)?.replace(/\/$/, "");
  if (configured) return `${configured}/ws/${encodeURIComponent(threadId)}`;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/${encodeURIComponent(threadId)}`;
}
