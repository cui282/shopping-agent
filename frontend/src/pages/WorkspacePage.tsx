import { useEffect, useState } from "react";
import { PanelRightOpen } from "lucide-react";
import { api } from "../api/client";
import ActivityRail from "../components/ActivityRail";
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
  const { state, startTask, cancelTask, loadThread, reset, clearDeletedThread, reconnect, refreshReadiness } =
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
  const busy = ["starting", "connecting", "running"].includes(state.status);
  const canCancel = Boolean(state.threadId) && ["connecting", "running"].includes(state.status);
  const disabledReason = taskDisabledReason(state.serviceStatus, state.readiness);
  const allowImageUpload = state.readiness?.capabilities.image_analysis === true;

  useEffect(() => {
    if (state.query && (state.status !== "idle" || !draft)) setDraft(state.query);
  }, [state.query, state.status]);

  useEffect(() => {
    if (state.threadId) updateStatus(state.threadId, state.status, state.providerMode);
  }, [state.threadId, state.status, state.providerMode, updateStatus]);

  const clearWorkspace = () => {
    setDraft("");
    setResultView("recommendations");
    setMobileView("workspace");
    setComposerResetKey((value) => value + 1);
  };

  const newResearch = () => {
    reset();
    clearWorkspace();
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
      status: "running",
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
    try {
      await api.deleteTask(session.threadId);
      remove(session.threadId);
      if (clearDeletedThread(session.threadId)) clearWorkspace();
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "删除失败，请重试");
    } finally {
      setDeletingThreadId(null);
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
            />
          </div>
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
        </main>

        <div className={styles.activityPane}>
          <ActivityRail
            state={state}
            userId={userId}
            preferenceStore={state.readiness?.preference_store}
            onClose={() => setActivityOpen(false)}
          />
        </div>
      </div>
    </div>
  );
}
