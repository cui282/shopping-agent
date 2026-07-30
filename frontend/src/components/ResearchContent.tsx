import { useState } from "react";
import { AlertTriangle, ArrowUpRight, Calculator, Download, ImageOff, RotateCcw, Star, Truck } from "lucide-react";
import { resolveApiUrl, safeExternalUrl } from "../api/client";
import type { AgentState } from "../hooks/useShoppingAgent";
import type { Recommendation } from "../types/api";
import { starterQueries } from "../data/starterQueries";
import { currencyCny, formatCount } from "../utils/format";
import {
  providerModeLabel,
  providerNameLabel,
  providerReasonLabel,
  providerSourceLabel,
  providerStatusLabel,
} from "../utils/trust";
import styles from "./ResearchContent.module.css";

export type ResultView = "recommendations" | "comparison";

interface ResearchContentProps {
  state: AgentState;
  view: ResultView;
  onViewChange: (view: ResultView) => void;
  onUseStarter: (query: string) => void;
  onReset: () => void;
}

function ProductImage({ product }: { product: Recommendation }) {
  const [failed, setFailed] = useState(false);
  if (!product.image_url || failed) {
    return (
      <div className={styles.imageFallback} aria-label={`${product.title} 暂无商品图`}>
        <ImageOff size={22} aria-hidden="true" />
        <span>暂无商品图</span>
      </div>
    );
  }
  return (
    <img
      className={styles.productImage}
      src={product.image_url}
      width="480"
      height="360"
      loading="lazy"
      alt={product.title}
      onError={() => setFailed(true)}
    />
  );
}

function ProductCard({ product }: { product: Recommendation }) {
  const attributes = Object.entries(product.attributes ?? {})
    .filter(([key, value]) => value != null && key !== "sandbox")
    .slice(0, 3);
  const attributeLabels: Record<string, string> = {
    weight_kg: "重量",
    material: "材质",
    style: "风格",
    color: "颜色",
    battery_hours: "续航",
    storage: "存储",
    display: "屏幕",
  };
  const productUrl = safeExternalUrl(product.product_url);
  const fixture = product.source === "fixture";

  return (
    <article className={styles.productCard}>
      <div className={styles.imageWrap}>
        <ProductImage product={product} />
        <span className={styles.rank} aria-label={`推荐第 ${product.rank} 名`}>
          {product.rank}
        </span>
        <span className={styles.platform}>{product.platform.toUpperCase()}</span>
      </div>
      <div className={styles.productBody}>
        <div className={styles.sourceLine} data-source={product.source}>
          {providerSourceLabel(product.source)}
        </div>
        <h3 title={product.title}>{product.title}</h3>
        <p className={styles.reason}>{product.reason}</p>
        {product.note && <p className={styles.sourceNote}>来源说明：{product.note}</p>}
        {attributes.length > 0 && (
          <dl className={styles.attributes}>
            {attributes.map(([key, value]) => (
              <div key={key}>
                <dt>{attributeLabels[key] ?? key}</dt>
                <dd>{key === "weight_kg" ? `${String(value)} kg` : String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
        <div className={styles.cardFooter}>
          <div>
            <span className={styles.price}>{currencyCny.format(product.landed_cny)}</span>
            <span className={styles.priceNote}>
              预估到手 · 商品价 {product.currency} {product.price.toLocaleString("zh-CN")}
            </span>
          </div>
          {productUrl && (
            <a
              className={styles.externalLink}
              href={productUrl}
              target="_blank"
              rel="noreferrer"
              aria-label={fixture ? `在 ${product.platform} 搜索 ${product.title}` : `前往 ${product.platform} 查看 ${product.title}`}
              title={fixture ? "在平台搜索相似商品" : "前往商品页"}
            >
              <span>{fixture ? "平台搜索" : "查看商品"}</span>
              <ArrowUpRight size={17} aria-hidden="true" />
            </a>
          )}
        </div>
        <div className={styles.commerceMeta}>
          {product.rating == null ? (
            <span>评分未提供</span>
          ) : (
            <span className={styles.rating}>
              <Star size={13} fill="currentColor" aria-hidden="true" /> {product.rating.toFixed(1)}
            </span>
          )}
          <span>{product.sales == null ? "销量未提供" : `${formatCount(product.sales)} 人购买`}</span>
          <span>
            <Truck size={13} aria-hidden="true" /> {product.eta_days} 天
          </span>
        </div>
      </div>
    </article>
  );
}

function StarterState({ onUseStarter }: { onUseStarter: (query: string) => void }) {
  return (
    <section className={styles.starter} aria-labelledby="starter-title">
      <div className={styles.starterIntro}>
        <span className={styles.eyebrow}>新的研究</span>
        <h2 id="starter-title">跨平台购物研究</h2>
      </div>
      <div className={styles.starterList}>
        {starterQueries.map((starter) => (
          <button key={starter.title} className={styles.starterItem} type="button" onClick={() => onUseStarter(starter.query)}>
            <img src={starter.image} width="136" height="102" alt={starter.imageAlt} />
            <span>
              <strong>{starter.title}</strong>
              <small>{starter.query}</small>
            </span>
            <ArrowUpRight size={18} aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>
  );
}

function WaitingState({ eventCount }: { eventCount: number }) {
  return (
    <section className={styles.waiting} aria-label="研究进行中" aria-busy="true">
      <div className={styles.pulseMark} aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <h2>{eventCount > 2 ? "正在收拢价格与偏好" : "正在打开各平台的候选集"}</h2>
      <p>{eventCount > 0 ? `已收到 ${eventCount} 条研究进度` : "任务已提交，正在等待第一条进度"}</p>
      <div className={styles.skeletonGrid} aria-hidden="true">
        {[0, 1, 2].map((value) => (
          <div className={styles.skeletonCard} key={value}>
            <span className={styles.skeletonImage} />
            <span className={styles.skeletonLine} />
            <span className={styles.skeletonLineShort} />
          </div>
        ))}
      </div>
    </section>
  );
}

function Comparison({ state }: { state: AgentState }) {
  const rows = state.result?.comparison ?? [];
  if (rows.length === 0) {
    return <p className={styles.noComparison}>本次结果没有可用的横向价格数据。</p>;
  }
  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.comparisonTable}>
          <thead>
            <tr>
              <th scope="col">商品</th>
              <th scope="col">平台</th>
              <th scope="col">来源</th>
              <th scope="col">商品价</th>
              <th scope="col">运费</th>
              <th scope="col">税费</th>
              <th scope="col">到手价</th>
              <th scope="col">时效</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.platform}-${row.item_id}`}>
                <th scope="row">{row.title}</th>
                <td>{row.platform.toUpperCase()}</td>
                <td>{providerSourceLabel(row.source)}</td>
                <td>{currencyCny.format(row.price_cny)}</td>
                <td>{row.shipping_cny == null ? "待确认" : currencyCny.format(row.shipping_cny)}</td>
                <td>{row.duty_cny == null ? "待确认" : currencyCny.format(row.duty_cny)}</td>
                <td className={styles.tablePrice}>{currencyCny.format(row.landed_cny ?? row.price_cny)}</td>
                <td>{row.eta_days == null ? "待确认" : `${row.eta_days} 天`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={styles.mobileComparison} aria-hidden="true">
        {rows.map((row) => (
          <article key={`${row.platform}-${row.item_id}`}>
            <header>
              <strong>{row.title}</strong>
              <span>
                {row.platform.toUpperCase()} · {providerSourceLabel(row.source)}
              </span>
            </header>
            <dl>
              <div>
                <dt>商品价</dt>
                <dd>{currencyCny.format(row.price_cny)}</dd>
              </div>
              <div>
                <dt>运税</dt>
                <dd>
                  {row.shipping_cny == null || row.duty_cny == null
                    ? "待确认"
                    : currencyCny.format(row.shipping_cny + row.duty_cny)}
                </dd>
              </div>
              <div>
                <dt>到手价</dt>
                <dd>{currencyCny.format(row.landed_cny ?? row.price_cny)}</dd>
              </div>
              <div>
                <dt>时效</dt>
                <dd>{row.eta_days == null ? "待确认" : `${row.eta_days} 天`}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </>
  );
}

function ProviderDisclosure({ state }: { state: AgentState }) {
  const providers = Object.entries(state.result?.providers ?? {});
  return (
    <section className={styles.providerDisclosure} aria-labelledby="provider-heading">
      <div>
        <h3 id="provider-heading">数据提供方</h3>
        <span>{providers.length ? `${providers.length} 项来源明细` : "本次结果未返回提供方明细"}</span>
      </div>
      {providers.length > 0 && (
        <ul>
          {providers.map(([name, metadata]) => (
            <li key={name} data-status={metadata.status}>
              <strong>{providerNameLabel(name)}</strong>
              <span>
                {providerSourceLabel(metadata.source)} · {providerStatusLabel(metadata.status)} · {metadata.provider}
              </span>
              {metadata.fallback_reason && <small>{providerReasonLabel(metadata.fallback_reason)}</small>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function ResearchContent({ state, view, onViewChange, onUseStarter, onReset }: ResearchContentProps) {
  if (state.status === "idle") return <StarterState onUseStarter={onUseStarter} />;
  if (["starting", "connecting", "running"].includes(state.status)) return <WaitingState eventCount={state.events.length} />;
  if (state.status === "error") {
    return (
      <section className={styles.terminalState} role="alert">
        <AlertTriangle size={26} aria-hidden="true" />
        <h2>这次研究没有完成</h2>
        <p>{state.error ?? "后端未返回可用结果，请检查服务后重新提交。"}</p>
        <button type="button" onClick={onReset}>
          <RotateCcw size={16} aria-hidden="true" /> 新建研究
        </button>
      </section>
    );
  }
  if (state.status === "cancelled") {
    return (
      <section className={styles.terminalState}>
        <h2>研究已取消</h2>
        <p>已收到的过程信息仍保留在右侧，未生成不完整的推荐。</p>
        <button type="button" onClick={onReset}>
          <RotateCcw size={16} aria-hidden="true" /> 重新开始
        </button>
      </section>
    );
  }

  const result = state.result;
  const moveResultTab = (next: ResultView) => {
    onViewChange(next);
    window.setTimeout(() => document.getElementById(`${next}-tab`)?.focus(), 0);
  };
  return (
    <section className={styles.results} aria-labelledby="result-heading">
      <div className={styles.resultHeader}>
        <div>
          <span className={styles.eyebrow}>
            {providerModeLabel(result?.provider_mode ?? "unverified")}
          </span>
          <h2 id="result-heading">购物建议</h2>
        </div>
        <div className={styles.segmented} role="tablist" aria-label="结果视图">
          <button
            id="recommendations-tab"
            type="button"
            role="tab"
            aria-selected={view === "recommendations"}
            aria-controls="result-panel"
            tabIndex={view === "recommendations" ? 0 : -1}
            data-active={view === "recommendations"}
            onClick={() => onViewChange("recommendations")}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                event.preventDefault();
                moveResultTab("comparison");
              }
            }}
          >
            推荐 {result?.recommendations.length ?? 0}
          </button>
          <button
            id="comparison-tab"
            type="button"
            role="tab"
            aria-selected={view === "comparison"}
            aria-controls="result-panel"
            tabIndex={view === "comparison" ? 0 : -1}
            data-active={view === "comparison"}
            onClick={() => onViewChange("comparison")}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                event.preventDefault();
                moveResultTab("recommendations");
              }
            }}
          >
            价格对比
          </button>
        </div>
      </div>

      {result?.final_answer && <p className={styles.summary}>{result.final_answer}</p>}
      {result?.calculation_notice && (
        <p className={styles.calculationNotice}>
          <Calculator size={16} aria-hidden="true" />
          <span>{result.calculation_notice}</span>
        </p>
      )}
      <ProviderDisclosure state={state} />

      <div
        id="result-panel"
        role="tabpanel"
        aria-labelledby={view === "recommendations" ? "recommendations-tab" : "comparison-tab"}
        tabIndex={0}
      >
        {view === "recommendations" ? (
          result?.recommendations.length ? (
            <div className={styles.productGrid}>
              {result.recommendations.map((product) => (
                <ProductCard key={`${product.platform}-${product.item_id}`} product={product} />
              ))}
            </div>
          ) : (
            <p className={styles.noComparison}>研究已完成，但没有商品同时满足硬性条件。</p>
          )
        ) : (
          <Comparison state={state} />
        )}
      </div>

      {result?.files.length ? (
        <div className={styles.downloads} aria-label="研究报告">
          <span>研究报告</span>
          {result.files.map((file) => {
            const url = resolveApiUrl(file.url);
            return url ? (
              <a key={file.url} href={url} download>
                <Download size={16} aria-hidden="true" /> {file.name}
              </a>
            ) : (
              <span className={styles.invalidDownload} key={file.url}>
                {file.name} 链接不可用
              </span>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
