import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import BrandMark from "../components/BrandMark";
import styles from "./StaticPage.module.css";

export default function StaticPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className={styles.page} id="main-content">
      <header>
        <BrandMark />
        <Link to="/">
          <ArrowLeft size={16} aria-hidden="true" /> 返回研究台
        </Link>
      </header>
      <article>
        <span>Shopping Agent</span>
        <h1>{title}</h1>
        {children}
        <p className={styles.updated}>更新于 2026 年 7 月 30 日</p>
      </article>
    </main>
  );
}
