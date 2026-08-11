import { Link } from "react-router-dom";
import { CirclePlus, GitBranch, History, LoaderCircle, Trash2 } from "lucide-react";
import type { SessionHistoryItem } from "../types/api";
import { formatRelativeTime, statusLabel } from "../utils/format";
import { providerModeLabel } from "../utils/trust";
import BrandMark from "./BrandMark";
import styles from "./SessionRail.module.css";

interface SessionRailProps {
  history: SessionHistoryItem[];
  activeThreadId: string | null;
  providerMode: string;
  onNew: () => void;
  onSelect: (session: SessionHistoryItem) => void;
  onDelete: (session: SessionHistoryItem) => void;
  deletingThreadId?: string | null;
  historyError?: string | null;
  historyNotice?: string | null;
}

export default function SessionRail({
  history,
  activeThreadId,
  providerMode,
  onNew,
  onSelect,
  onDelete,
  deletingThreadId = null,
  historyError = null,
  historyNotice = null,
}: SessionRailProps) {
  const providerLabel = providerModeLabel(providerMode);

  return (
    <aside className={styles.rail} aria-label="研究会话">
      <div className={styles.header}>
        <BrandMark />
        <button className={styles.newButton} type="button" onClick={onNew}>
          <CirclePlus size={17} aria-hidden="true" />
          新研究
        </button>
      </div>

      <section
        className={styles.history}
        aria-labelledby="history-heading"
        aria-busy={Boolean(deletingThreadId)}
      >
        <div className={styles.sectionLabel}>
          <History size={14} aria-hidden="true" />
          <h2 id="history-heading">最近研究</h2>
        </div>
        {history.length === 0 ? (
          <p className={styles.empty}>暂无最近研究</p>
        ) : (
          <ol className={styles.list}>
            {history.map((session) => (
              <li
                className={styles.sessionRow}
                data-active={session.threadId === activeThreadId}
                key={session.threadId}
              >
                <button
                  className={styles.session}
                  type="button"
                  onClick={() => onSelect(session)}
                  disabled={deletingThreadId === session.threadId}
                  data-session-select="true"
                  aria-current={session.threadId === activeThreadId ? "page" : undefined}
                >
                  <span className={styles.query}>{session.query}</span>
                  {session.lineage && (
                    <span className={styles.lineage}>
                      <GitBranch size={12} aria-hidden="true" />
                      {session.lineage.relation === "constraint_relaxation" ? "放宽条件" : "重新研究"}
                      {` · 第 ${session.lineage.depth} 代`}
                    </span>
                  )}
                  <span className={styles.meta}>
                    <span className={styles.statusDot} data-status={session.status} aria-hidden="true" />
                    {statusLabel(session.status)} · {formatRelativeTime(session.createdAt)}
                  </span>
                </button>
                <button
                  className={styles.deleteButton}
                  type="button"
                  onClick={() => onDelete(session)}
                  disabled={deletingThreadId === session.threadId}
                  aria-label={
                    deletingThreadId === session.threadId
                      ? `正在删除研究：${session.query}`
                      : `删除研究：${session.query}`
                  }
                  title="删除研究"
                >
                  {deletingThreadId === session.threadId ? (
                    <LoaderCircle className={styles.spinner} size={16} aria-hidden="true" />
                  ) : (
                    <Trash2 size={16} aria-hidden="true" />
                  )}
                </button>
              </li>
            ))}
          </ol>
        )}
        {deletingThreadId && (
          <p className={styles.historyProgress} role="status" aria-live="polite">
            正在删除研究：
            {history.find((session) => session.threadId === deletingThreadId)?.query ?? "当前研究"}
          </p>
        )}
        {historyError && (
          <p className={styles.historyError} role="alert">
            {historyError}
          </p>
        )}
        {historyNotice && (
          <p className={styles.historyNotice} role="status" aria-live="polite">
            {historyNotice}
          </p>
        )}
      </section>

      <footer className={styles.footer}>
        <div className={styles.provider} data-mode={providerMode}>
          <span className={styles.providerDot} aria-hidden="true" />
          <span>{providerLabel}</span>
        </div>
        <div className={styles.footerLinks}>
          <Link to="/privacy">隐私</Link>
          <Link to="/terms">条款</Link>
        </div>
      </footer>
    </aside>
  );
}
