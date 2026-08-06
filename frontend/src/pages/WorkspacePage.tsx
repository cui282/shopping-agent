import { useEffect, useRef, useState } from "react";
import { PanelRightOpen } from "lucide-react";
import { api } from "../api/client";
import ActivityRail from "../components/ActivityRail";
import ClarificationPanel from "../components/ClarificationPanel";
import MobileHeader, { type MobileView } from "../components/MobileHeader";
import QueryComposer from "../components/QueryComposer";
import ReadinessNotice from "../components/ReadinessNotice";
import ResearchContent, { type ResultView } from "../components/ResearchContent";
import SessionRail from "../components/SessionRail";
import StatusBar from "../components/StatusBar";
import { useSessionHistory } from "../hooks/useSessionHistory";
import { useShoppingAgent } from "../hooks/useShoppingAgent";
import type { SessionHistoryItem } from "../types/api";
import { prepareShoppingQuery } from "../utils/queryContract";
import { getAnonymousUserId } from "../utils/anonymousUser";
import { taskDisabledReason } from "../utils/trust";
import styles from "./WorkspacePage.module.css";

export default function WorkspacePage() {
  const {
    state,
    startTask,
    cancelTask,
    respondToClarification,
    loadThread,
    reset,
    clearDeletedThread,
    reconnect,
    refreshReadiness,
  } =
    useShoppingAgent();
  const { history, upsert, updateStatus, remove } = useSessionHistory();
  const [userId] = useState(getAnonymousUserId);
  const [draft, setDraft] = useState("");
  const [resultView, setResultView] = useState<ResultView>("recommendations");
  const [mobileView, setMobileView] = useState<MobileView>("workspace");
  const [activityOpen, setActivityOpen] = useState(true);
  const [composerResetKey, setComposerResetKey] = useState(0);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyNotice, setHistoryNotice] = useState<string | null>(null);
  const [commandPending, setCommandPending] = useState<"rerun" | "relaxation" | null>(null);
  const rerunCommandKeysRef = useRef(new Map<string, string>());
  const relaxationCommandKeysRef = useRef(new Map<string, string>());
  const historyLengthRef = useRef(history.length);
  const wasAwaitingClarificationRef = useRef(false);
  const busy = ["starting", "connecting", "running"].includes(state.status);
  const canCancel = Boolean(state.threadId) && ["connecting", "running", "awaiting_clarification"].includes(state.status);
  const disabledReason = taskDisabledReason(state.serviceStatus, state.readiness);
  const allowImageUpload = state.readiness?.capabilities.image_analysis === true;

  const focusComposer = () => {
    window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('textarea[name="shopping-query"]')?.focus(), 0);
  };

  useEffect(() => {
    const wasAwaitingClarification = wasAwaitingClarificationRef.current;
    wasAwaitingClarificationRef.current = state.status === "awaiting_clarification";
    if (wasAwaitingClarification && state.status !== "awaiting_clarification") focusComposer();
  }, [state.status]);

  useEffect(() => {
    if (state.query && (state.status !== "idle" || !draft)) setDraft(state.query);
  }, [state.query, state.status]);

  useEffect(() => {
    if (state.threadId) {
      updateStatus(
        state.threadId,
        state.status,
        state.providerMode,
        state.snapshot?.lineage,
        state.result?.mode ?? state.snapshot?.resolved_intent?.mode ?? undefined,
      );
    }
  }, [
    state.threadId,
    state.status,
    state.providerMode,
    state.snapshot?.lineage,
    state.result?.mode,
    state.snapshot?.resolved_intent?.mode,
    updateStatus,
  ]);

  useEffect(() => {
    historyLengthRef.current = history.length;
  }, [history.length]);

  useEffect(() => {
    let active = true;
    void api.recentResearch(userId)
      .then((response) => {
        if (!active) return;
        for (const snapshot of [...response.snapshots].reverse()) {
          upsert({
            threadId: snapshot.thread_id,
            query: snapshot.query,
            status: snapshot.status,
            createdAt: snapshot.created_at,
            providerMode: snapshot.result?.provider_mode ?? snapshot.data_mode,
            lineage: snapshot.lineage,
            mode: snapshot.result?.mode ?? snapshot.resolved_intent?.mode,
          });
        }
      })
      .catch(() => {
        if (active && historyLengthRef.current === 0) setHistoryError("无法加载最近研究，请刷新后重试");
      });
    return () => {
      active = false;
    };
  }, [upsert, userId]);

  useEffect(() => {
    if (!state.threadId || !state.query) return;
    upsert({
      threadId: state.threadId,
      query: state.query,
      status: state.status,
      createdAt: state.snapshot?.created_at ?? new Date().toISOString(),
      providerMode: state.result?.provider_mode ?? state.providerMode,
      lineage: state.snapshot?.lineage,
      mode: state.result?.mode ?? state.snapshot?.resolved_intent?.mode,
    });
  }, [state.threadId, state.query, state.status, state.providerMode, state.result, state.snapshot, upsert]);

  const clearWorkspace = () => {
    setDraft("");
    setResultView("recommendations");
    setMobileView("workspace");
    setComposerResetKey((value) => value + 1);
  };

  const newResearch = () => {
    reset();
    clearWorkspace();
    setHistoryNotice(null);
  };

  const submit = async (uploadIds: string[]) => {
    const prepared = prepareShoppingQuery(draft);
    if (!prepared.query) return;
    const query = prepared.query;
    const threadId = await startTask({ query, user_id: userId, upload_ids: uploadIds, thread_id: null });
    if (!threadId) return;
    upsert({
      threadId,
      query,
      status: state.status === "awaiting_clarification" ? "awaiting_clarification" : "running",
      createdAt: new Date().toISOString(),
      providerMode: state.providerMode,
    });
    setResultView("recommendations");
    setMobileView("workspace");
    setComposerResetKey((value) => value + 1);
  };

  const selectSession = (session: SessionHistoryItem) => {
    setDraft(session.query);
    setResultView("recommendations");
    setMobileView("workspace");
    void loadThread(session.threadId, session.query);
  };

  const deleteSession = async (session: SessionHistoryItem) => {
    if (deletingThreadId || !window.confirm(`删除“${session.query}”及其研究报告？此操作无法撤销。`)) return;
    setDeletingThreadId(session.threadId);
    setHistoryError(null);
    setHistoryNotice(null);
    try {
      await api.deleteTask(session.threadId, userId);
      remove(session.threadId);
      if (clearDeletedThread(session.threadId)) {
        clearWorkspace();
        focusComposer();
      } else {
        window.setTimeout(() => {
          document.querySelector<HTMLButtonElement>('[data-session-select="true"]')?.focus();
        }, 0);
      }
      setHistoryNotice("研究已删除");
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "删除失败，请重试");
    } finally {
      setDeletingThreadId(null);
    }
  };

  const submitClarification = (response: string) => respondToClarification(response);

  const idempotencyKey = () =>
    typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;

  const rerunResearch = async () => {
    if (!state.threadId || state.status !== "completed" || commandPending) return;
    const parentThreadId = state.threadId;
    const commandKey = rerunCommandKeysRef.current.get(parentThreadId) ?? idempotencyKey();
    rerunCommandKeysRef.current.set(parentThreadId, commandKey);
    setCommandPending("rerun");
    setHistoryError(null);
    try {
      const response = await api.rerunTask(state.threadId, userId, commandKey);
      upsert({
        threadId: response.thread_id,
        query: state.query,
        status: "running",
        createdAt: new Date().toISOString(),
        providerMode: state.providerMode,
        lineage: response.lineage,
        mode: state.result?.mode,
      });
      await loadThread(response.thread_id, state.query);
      setMobileView("workspace");
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "Research Rerun 启动失败");
    } finally {
      setCommandPending(null);
    }
  };

  const relaxConstraint = async (constraintId: string) => {
    if (!state.threadId || state.status !== "completed" || commandPending) return;
    if (!window.confirm("确认放宽这项 Hard Constraint 并开始新的 Shopping Research Task？")) return;
    const commandScope = `${state.threadId}:${constraintId}`;
    const commandKey = relaxationCommandKeysRef.current.get(commandScope) ?? idempotencyKey();
    relaxationCommandKeysRef.current.set(commandScope, commandKey);
    setCommandPending("relaxation");
    setHistoryError(null);
    try {
      const response = await api.relaxTask(state.threadId, userId, {
        confirmed: true,
        constraint_ids: [constraintId],
        idempotency_key: commandKey,
      });
      upsert({
        threadId: response.thread_id,
        query: state.query,
        status: "running",
        createdAt: new Date().toISOString(),
        providerMode: state.providerMode,
        lineage: response.lineage,
        mode: state.result?.mode,
      });
      await loadThread(response.thread_id, state.query);
      setMobileView("workspace");
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "Constraint Relaxation 启动失败");
    } finally {
      setCommandPending(null);
    }
  };

  const currentTitle = state.query || "新的购物研究";
  return (
    <div className={styles.app}>
      <MobileHeader view={mobileView} onChange={setMobileView} />
      <div className={styles.shell} data-mobile-view={mobileView} data-activity-open={activityOpen}>
        <div className={styles.sessionPane}>
          <SessionRail
            history={history}
            activeThreadId={state.threadId}
            providerMode={state.providerMode}
            onNew={newResearch}
            onSelect={selectSession}
            onDelete={(session) => void deleteSession(session)}
            deletingThreadId={deletingThreadId}
            historyError={historyError}
            historyNotice={historyNotice}
          />
        </div>

        <main className={styles.mainPane} id="main-content" tabIndex={-1}>
          <header className={styles.workspaceHeader}>
            <div className={styles.titleBlock}>
              <span>{state.threadId ? `任务 ${state.threadId.slice(-8)}` : "购物研究"}</span>
              <h1 title={currentTitle}>{currentTitle}</h1>
            </div>
            <div className={styles.headerActions}>
              <StatusBar state={state} onReconnect={() => state.threadId && reconnect(state.threadId, state.query)} />
              {!activityOpen && (
                <button
                  className={styles.openActivity}
                  type="button"
                  onClick={() => setActivityOpen(true)}
                  aria-label="打开过程面板"
                  title="打开过程面板"
                >
                  <PanelRightOpen size={18} aria-hidden="true" />
                </button>
              )}
            </div>
          </header>

          <ReadinessNotice state={state} onRefresh={() => void refreshReadiness()} />

          <div className={styles.scrollArea}>
            <ResearchContent
              state={state}
              view={resultView}
              onViewChange={setResultView}
              onUseStarter={(query) => {
                setDraft(query);
                window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('textarea[name="shopping-query"]')?.focus(), 0);
              }}
              onReset={newResearch}
              onRerun={state.status === "completed" && !commandPending ? () => void rerunResearch() : undefined}
              onRelax={state.status === "completed" && !commandPending ? (constraintId) => void relaxConstraint(constraintId) : undefined}
            />
          </div>
          {state.status === "awaiting_clarification" && state.clarification ? (
            <ClarificationPanel
              prompt={state.clarification}
              onSubmit={submitClarification}
              onCancel={cancelTask}
              onRestoreFocus={focusComposer}
            />
          ) : (
            <QueryComposer
              value={draft}
              busy={busy}
              canCancel={canCancel}
              disabledReason={disabledReason}
              allowImageUpload={allowImageUpload}
              attachmentResetKey={composerResetKey}
              onChange={setDraft}
              onSubmit={(ids) => void submit(ids)}
              onCancel={() => void cancelTask()}
            />
          )}
        </main>

        <div className={styles.activityPane}>
          <ActivityRail
            state={state}
            userId={userId}
            preferenceStore={state.readiness?.preference_store}
            preferenceBackend={state.readiness?.preference_backend}
            onClose={() => setActivityOpen(false)}
          />
        </div>
      </div>
    </div>
  );
}
