import { Link } from "react-router-dom";
import { CirclePlus, History } from "lucide-react";
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
}

export default function SessionRail({ history, activeThreadId, providerMode, onNew, onSelect }: SessionRailProps) {
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

      <section className={styles.history} aria-labelledby="history-heading">
        <div className={styles.sectionLabel}>
          <History size={14} aria-hidden="true" />
          <h2 id="history-heading">最近研究</h2>
        </div>
        {history.length === 0 ? (
          <p className={styles.empty}>暂无最近研究</p>
        ) : (
          <ol className={styles.list}>
            {history.map((session) => (
              <li key={session.threadId}>
                <button
                  className={styles.session}
                  data-active={session.threadId === activeThreadId}
                  type="button"
                  onClick={() => onSelect(session)}
                  aria-current={session.threadId === activeThreadId ? "page" : undefined}
                >
                  <span className={styles.query}>{session.query}</span>
                  <span className={styles.meta}>
                    <span className={styles.statusDot} data-status={session.status} aria-hidden="true" />
                    {statusLabel(session.status)} · {formatRelativeTime(session.createdAt)}
                  </span>
                </button>
              </li>
            ))}
          </ol>
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
