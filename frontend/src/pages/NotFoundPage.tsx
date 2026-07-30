import { ArrowLeft, Search } from "lucide-react";
import { Link } from "react-router-dom";
import BrandMark from "../components/BrandMark";
import styles from "./StaticPage.module.css";

export default function NotFoundPage() {
  return (
    <main className={styles.notFound} id="main-content">
      <BrandMark />
      <div className={styles.errorCode}>404</div>
      <Search size={28} aria-hidden="true" />
      <h1>这里没有购物研究</h1>
      <p>链接可能已移动，返回工作台可以新建或继续最近的研究。</p>
      <Link className={styles.primaryLink} to="/">
        <ArrowLeft size={16} aria-hidden="true" /> 返回研究台
      </Link>
    </main>
  );
}
