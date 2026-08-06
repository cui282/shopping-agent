import { useEffect, useId, useRef, useState } from "react";
import { LoaderCircle, Send, Square } from "lucide-react";
import type { ClarificationPrompt } from "../types/api";
import styles from "./ClarificationPanel.module.css";

interface ClarificationPanelProps {
  prompt: ClarificationPrompt;
  onSubmit: (response: string) => Promise<{ ok: boolean; message?: string }>;
  onCancel: () => void | Promise<void>;
  onRestoreFocus: () => void;
}

export default function ClarificationPanel({
  prompt,
  onSubmit,
  onCancel,
  onRestoreFocus,
}: ClarificationPanelProps) {
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const errorId = useId();
  const statusId = `${errorId}-status`;

  useEffect(() => {
    setAnswer("");
    setError(null);
    setSubmitting(false);
    inputRef.current?.focus();
  }, [prompt.field, prompt.question]);

  const submit = async () => {
    const value = answer.trim();
    if (!value || submitting) {
      inputRef.current?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await onSubmit(value);
      if (result.ok) {
        onRestoreFocus();
        return;
      }
      setSubmitting(false);
      setError(result.message ?? "回答未被接受，请重新输入");
    } catch (submitError) {
      setSubmitting(false);
      setError(submitError instanceof Error ? submitError.message : "回答未被接受，请重新输入");
    }
    inputRef.current?.focus();
  };

  const cancel = async () => {
    if (submitting) return;
    await onCancel();
    onRestoreFocus();
  };

  return (
    <section className={styles.panel} aria-labelledby={`${errorId}-question`} aria-busy={submitting}>
      <div className={styles.heading}>
        <span className={styles.eyebrow}>需要确认</span>
        <h2 id={`${errorId}-question`}>{prompt.question}</h2>
      </div>
      <div className={styles.inputRow} data-error={Boolean(error)}>
        <label className={styles.visuallyHidden} htmlFor={`${errorId}-answer`}>
          澄清回答
        </label>
        <input
          ref={inputRef}
          id={`${errorId}-answer`}
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              void cancel();
            } else if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder="输入回答"
          autoComplete="off"
          disabled={submitting}
          aria-describedby={statusId}
          aria-invalid={Boolean(error)}
        />
        <div className={styles.actions}>
          <button type="button" className={styles.cancelButton} onClick={() => void cancel()} disabled={submitting} aria-label="取消研究" title="取消研究">
            <Square size={15} fill="currentColor" aria-hidden="true" />
          </button>
          <button
            type="button"
            className={styles.submitButton}
            onClick={() => void submit()}
            disabled={submitting || !answer.trim()}
            aria-label="提交回答"
            title="提交回答"
          >
            {submitting ? <LoaderCircle className={styles.spinning} size={18} aria-hidden="true" /> : <Send size={18} aria-hidden="true" />}
          </button>
        </div>
      </div>
      <div id={statusId} className={styles.message} role="status" aria-live="polite" aria-atomic="true">
        {error ?? "回答后将继续这次研究"}
      </div>
    </section>
  );
}
