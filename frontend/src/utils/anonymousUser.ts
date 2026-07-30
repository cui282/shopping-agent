const STORAGE_KEY = "shopping-agent.anonymous-user.v1";

let memoryUserId: string | null = null;

type UserStorage = Pick<Storage, "getItem" | "setItem">;

export function createAnonymousUserId(randomUUID?: () => string): string {
  const generate =
    randomUUID ??
    (() => {
      if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
      if (globalThis.crypto?.getRandomValues) {
        const values = new Uint32Array(4);
        globalThis.crypto.getRandomValues(values);
        return Array.from(values, (value) => value.toString(16).padStart(8, "0")).join("-");
      }
      return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    });
  return `anon-${generate().replace(/[^A-Za-z0-9_-]/g, "")}`.slice(0, 120);
}

export function getAnonymousUserId(storage?: UserStorage | null, randomUUID?: () => string): string {
  let target = storage;
  if (target === undefined) {
    try {
      target = typeof window === "undefined" ? null : window.localStorage;
    } catch {
      target = null;
    }
  }

  try {
    const saved = target?.getItem(STORAGE_KEY);
    if (saved && /^[A-Za-z0-9_-]{1,120}$/.test(saved)) return saved;
  } catch {
    target = null;
  }

  if (!memoryUserId) memoryUserId = createAnonymousUserId(randomUUID);
  try {
    target?.setItem(STORAGE_KEY, memoryUserId);
  } catch {
    // The in-memory identity still keeps requests consistent for this page load.
  }
  return memoryUserId;
}
