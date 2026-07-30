import { useCallback, useState } from "react";
import type { SessionHistoryItem, TaskStatus } from "../types/api";

const STORAGE_KEY = "shopping-agent.session-history.v1";
const MAX_SESSIONS = 10;

export function withoutSession(history: SessionHistoryItem[], threadId: string): SessionHistoryItem[] {
  return history.filter((entry) => entry.threadId !== threadId);
}

function readHistory(): SessionHistoryItem[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as SessionHistoryItem[];
    return Array.isArray(parsed) ? parsed.slice(0, MAX_SESSIONS) : [];
  } catch {
    return [];
  }
}

export function useSessionHistory() {
  const [history, setHistory] = useState<SessionHistoryItem[]>(readHistory);

  const commit = useCallback((build: (current: SessionHistoryItem[]) => SessionHistoryItem[]) => {
    setHistory((current) => {
      const limited = build(current).slice(0, MAX_SESSIONS);
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(limited));
      } catch {
        // History remains available in memory when browser storage is unavailable.
      }
      return limited;
    });
  }, []);

  const upsert = useCallback(
    (item: SessionHistoryItem) => {
      commit((current) => [item, ...current.filter((entry) => entry.threadId !== item.threadId)]);
    },
    [commit],
  );

  const updateStatus = useCallback(
    (threadId: string, status: TaskStatus, providerMode?: string) => {
      commit((current) =>
        current.map((entry) => {
          if (entry.threadId !== threadId) return entry;
          if (entry.status === status && (!providerMode || entry.providerMode === providerMode)) return entry;
          return { ...entry, status, providerMode: providerMode ?? entry.providerMode };
        }),
      );
    },
    [commit],
  );

  const remove = useCallback(
    (threadId: string) => {
      commit((current) => withoutSession(current, threadId));
    },
    [commit],
  );

  return { history, upsert, updateStatus, remove };
}
