import { Activity, ListChecks, MessageSquareText } from "lucide-react";
import BrandMark from "./BrandMark";
import styles from "./MobileHeader.module.css";

export type MobileView = "sessions" | "workspace" | "activity";

interface MobileHeaderProps {
  view: MobileView;
  onChange: (view: MobileView) => void;
}

const tabs = [
  { value: "sessions" as const, label: "任务", Icon: ListChecks },
  { value: "workspace" as const, label: "研究", Icon: MessageSquareText },
  { value: "activity" as const, label: "过程", Icon: Activity },
];

export default function MobileHeader({ view, onChange }: MobileHeaderProps) {
  return (
    <header className={styles.header}>
      <BrandMark compact />
      <nav className={styles.tabs} aria-label="工作区视图">
        {tabs.map(({ value, label, Icon }) => (
          <button
            key={value}
            type="button"
            className={styles.tab}
            data-active={view === value}
            onClick={() => onChange(value)}
            aria-pressed={view === value}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>
    </header>
  );
}
