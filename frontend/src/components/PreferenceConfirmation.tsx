import { useEffect, useState } from "react";
import { BookmarkPlus, Check, LoaderCircle } from "lucide-react";
import styles from "./PreferenceConfirmation.module.css";

interface PreferenceConfirmationProps {
  value: string;
  onRemember: (value: string) => Promise<boolean>;
}

export default function PreferenceConfirmation({ value, onRemember }: PreferenceConfirmationProps) {
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  useEffect(() => setStatus("idle"), [value]);

  const remember = async () => {
    if (status === "saving" || status === "saved") return;
    setStatus("saving");
    setStatus((await onRemember(value)) ? "saved" : "error");
  };

  if (status === "saved") {
    return (
      <p className={styles.saved} role="status">
        <Check size={15} aria-hidden="true" /> 已保存为未来研究偏好
      </p>
    );
  }

  return (
    <section className={styles.prompt} aria-label="偏好确认">
      <div>
        <strong>这次研究识别到“{value}”风格</strong>
        <span>仅用于本次研究，不会自动记住。</span>
      </div>
      <button type="button" onClick={() => void remember()} disabled={status === "saving"}>
        {status === "saving" ? (
          <LoaderCircle className={styles.spinning} size={15} aria-hidden="true" />
        ) : (
          <BookmarkPlus size={15} aria-hidden="true" />
        )}
        以后也按“{value}”推荐
      </button>
      {status === "error" && <p role="alert">暂时无法保存，请稍后重试。</p>}
    </section>
  );
}
