import styles from "./BrandMark.module.css";

export default function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className={styles.brand} aria-label="Shopping Agent">
      <span className={styles.mark} aria-hidden="true">
        S
      </span>
      {!compact && (
        <span className={styles.wordmark}>
          Shopping Agent
          <small>购物研究台</small>
        </span>
      )}
    </div>
  );
}
