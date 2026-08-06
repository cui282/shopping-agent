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
    (
      threadId: string,
      status: TaskStatus,
      providerMode?: string,
      lineage?: SessionHistoryItem["lineage"],
      mode?: SessionHistoryItem["mode"],
    ) => {
      commit((current) =>
        current.map((entry) => {
          if (entry.threadId !== threadId) return entry;
          if (
            entry.status === status &&
            (!providerMode || entry.providerMode === providerMode) &&
            entry.lineage === lineage &&
            entry.mode === mode
          ) {
            return entry;
          }
          return {
            ...entry,
            status,
            providerMode: providerMode ?? entry.providerMode,
            lineage: lineage ?? entry.lineage,
            mode: mode ?? entry.mode,
          };
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
