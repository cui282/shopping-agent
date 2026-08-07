import { useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  Calculator,
  Download,
  ImageOff,
  ListOrdered,
  RefreshCw,
  RotateCcw,
  Star,
  Truck,
} from "lucide-react";
import { resolveApiUrl, safeExternalUrl } from "../api/client";
import type { AgentState } from "../hooks/useShoppingAgent";
import type {
  AlternativeCandidate,
  ConstraintEvaluation,
  CalculationExclusion,
  ConstraintExclusion,
  GeneratedFile,
  IdentityEvidence,
  PreferenceDecision,
  RankingDimension,
  RankingProfile,
  Recommendation,
  UnverifiedCandidate,
  WorkingAssumption,
} from "../types/api";
import { starterQueries } from "../data/starterQueries";
import { currencyCny, formatCount } from "../utils/format";
import {
  providerNameLabel,
  providerReasonLabel,
  providerSourceLabel,
  providerStatusLabel,
  recallChannelLabel,
  recallModeLabel,
  recallStateLabel,
  personalizationInputSourceLabel,
  personalizationSignalLabel,
  resultBadgeLabel,
} from "../utils/trust";
import styles from "./ResearchContent.module.css";

export type ResultView = "recommendations" | "comparison";

const reportFormatLabels: Record<string, string> = {
  markdown: "Markdown",
  json: "JSON",
  pdf: "PDF",
};

function reportFormat(file: GeneratedFile): string {
  if (file.format) return file.format;
  const extension = file.name.split(".").pop()?.toLowerCase();
  return extension === "md" ? "markdown" : extension ?? "report";
}

function ReportDownloads({ files }: { files: GeneratedFile[] }) {
  const [status, setStatus] = useState(
    files.length ? `${files.length} 种格式已准备` : "暂无报告可下载",
  );
  return (
    <div className={styles.downloads} aria-label="研究报告">
      <span>研究报告</span>
      <span className={styles.downloadStatus} role="status" aria-label="研究报告下载" aria-live="polite">
        {status}
      </span>
      {files.map((file) => {
        const format = reportFormat(file);
        const label = reportFormatLabels[format] ?? file.name;
        const url = resolveApiUrl(file.url);
        return url ? (
          <a
            className={styles.downloadLink}
            key={file.file_id ?? file.url}
            href={url}
            download={file.name}
            aria-label={`下载 ${label} 报告`}
            onClick={() => setStatus(`${label} 下载已开始`)}
          >
            <Download size={16} aria-hidden="true" /> {label}
          </a>
        ) : (
          <span className={styles.invalidDownload} role="alert" key={file.file_id ?? file.url}>
            {label} 报告链接不可用
          </span>
        );
      })}
    </div>
  );
}

interface ResearchContentProps {
  state: AgentState;
  view: ResultView;
  onViewChange: (view: ResultView) => void;
  onUseStarter: (query: string) => void;
  onReset: () => void;
  onRerun?: () => void;
  onRelax?: (constraintId: string) => void;
}

function ProductImage({ product }: { product: Recommendation }) {
  const [failed, setFailed] = useState(false);
  const imageUrl = safeExternalUrl(product.image_url);
  if (!imageUrl || failed) {
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
      src={imageUrl}
      width="480"
      height="360"
      loading="lazy"
      alt={product.title}
      onError={() => setFailed(true)}
    />
  );
}

const evidenceLabels: Record<string, string> = {
  weight_kg: "重量",
  material: "材质",
  style: "风格",
  color: "颜色",
  battery_hours: "续航",
  storage: "存储",
  display: "屏幕",
  capacity: "容量",
  condition: "成色",
};

const rankingLabels: Record<RankingDimension, string> = {
  landed_cost: "到手价",
  preference_match: "偏好匹配",
  evidence_quality: "证据质量",
  delivery_time: "配送时效",
};

const defaultRankingProfile: RankingProfile = {
  priority_order: ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
  explicit: false,
};

function researchModeLabel(mode: string | undefined): string {
  return mode === "exact_offer_comparison" ? "Exact Offer Comparison" : "Product Research";
}

function identityEvidenceLabel(evidence: IdentityEvidence | null | undefined): string {
  if (!evidence || evidence.decision === "not_required") {
    return "Product Research：无需同款证明";
  }
  if (evidence.decision === "matching_offer") {
    if (evidence.basis === "identifier") {
      const identifier = evidence.matched_fields.some((field) => field === "identity.gtin")
        ? "GTIN"
        : evidence.matched_fields.some((field) => field === "identity.mpn")
          ? "MPN"
          : "跨平台 identifier";
      return `${identifier} 已验证同款`;
    }
    return "关键属性已验证同款";
  }
  return "Identity Evidence 不足，列为替代候选";
}

function availabilityLabel(value: string | null): string {
  if (!value) return "未提供";
  return {
    in_stock: "有货",
    out_of_stock: "缺货",
    limited: "库存有限",
    preorder: "预售",
    backorder: "可订货",
  }[value] ?? value;
}

function retrievedAtLabel(value: string | null): string | null {
  if (!value) return null;
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(timestamp);
}

function estimateLabel(estimated: boolean | undefined): string {
  return estimated === false ? "实测" : "估算";
}

function estimateSource(source: string | undefined): string {
  return source ? `来源：${source}` : "来源未提供";
}

function ProductCard({ product }: { product: Recommendation }) {
  const variantAttributes = Object.entries(product.variant_attributes ?? {})
    .filter(([, value]) => value != null)
    .slice(0, 3);
  const identityEntries = [
    ["品牌", product.identity?.brand],
    ["型号", product.identity?.model],
    ["GTIN", product.identity?.gtin],
    ["MPN", product.identity?.mpn],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  const retrievedAt = retrievedAtLabel(product.retrieved_at);
  const productUrl = product.link_kind ? safeExternalUrl(product.product_url) : null;
  const searchLink = product.link_kind === "marketplace_search";
  const marketplace = providerNameLabel(product.marketplace ?? product.platform);
  const provider = product.provenance?.provider;

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
          {provider ? ` · ${provider}` : ""}
        </div>
        <h3 title={product.title}>{product.title}</h3>
        <p className={styles.reason}>{product.reason}</p>
        <dl className={styles.costBreakdown} aria-label={`${product.title} 中国大陆到手成本`}>
          <div>
            <dt>商品价（原币）</dt>
            <dd>
              {product.currency} {product.price.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </dd>
          </div>
          <div>
            <dt>商品价（CNY）</dt>
            <dd>{currencyCny.format(product.price_cny)}</dd>
          </div>
          <div>
            <dt>运费 {estimateLabel(product.shipping_estimate?.estimated)}</dt>
            <dd>
              {currencyCny.format(product.shipping_cny)}
              <small>{estimateSource(product.shipping_estimate?.source)}</small>
            </dd>
          </div>
          <div>
            <dt>关税 {estimateLabel(product.duty_estimate?.estimated)}</dt>
            <dd>
              {currencyCny.format(product.duty_cny)}
              <small>{estimateSource(product.duty_estimate?.source)}</small>
            </dd>
          </div>
        </dl>
        <dl className={styles.scoreBreakdown} aria-label={`${product.title} 排序分解`}>
          <div>
            <dt>到手价分</dt>
            <dd>{product.score_breakdown.landed_cost_score.toFixed(2)}</dd>
          </div>
          <div>
            <dt>偏好匹配分</dt>
            <dd>{product.score_breakdown.preference_match_score.toFixed(2)}</dd>
          </div>
          <div>
            <dt>证据质量分</dt>
            <dd>{product.score_breakdown.evidence_quality_score.toFixed(2)}</dd>
          </div>
          <div>
            <dt>时效分</dt>
            <dd>{product.score_breakdown.delivery_time_score.toFixed(2)}</dd>
          </div>
        </dl>
        <dl className={styles.evidence} aria-label={`${product.title} 商品证据`}>
          <div className={styles.evidenceWide}>
            <dt>Identity Evidence</dt>
            <dd>
              <strong>{identityEvidenceLabel(product.identity_evidence)}</strong>
              {product.identity_evidence?.explanation && (
                <small>{product.identity_evidence.explanation}</small>
              )}
            </dd>
          </div>
          <div>
            <dt>抓取时间</dt>
            <dd>
              {retrievedAt ? (
                <time dateTime={product.retrieved_at ?? undefined}>{retrievedAt}</time>
              ) : (
                "未提供"
              )}
            </dd>
          </div>
          <div>
            <dt>库存</dt>
            <dd>{availabilityLabel(product.availability)}</dd>
          </div>
          <div className={styles.evidenceWide}>
            <dt>上游来源</dt>
            <dd>{product.provenance?.upstream_source ?? "未提供"}</dd>
          </div>
          <div className={styles.evidenceWide}>
            <dt>Offer ID</dt>
            <dd>{product.offer_id ?? "未提供"}</dd>
          </div>
          {identityEntries.length ? (
            identityEntries.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))
          ) : (
            <div className={styles.evidenceWide}>
              <dt>跨平台标识</dt>
              <dd>未提供</dd>
            </div>
          )}
          {variantAttributes.map(([key, value]) => (
            <div key={key}>
              <dt>{evidenceLabels[key] ?? key}</dt>
              <dd>{key === "weight_kg" ? `${String(value)} kg` : String(value)}</dd>
            </div>
          ))}
        </dl>
        {product.note && <p className={styles.sourceNote}>来源说明：{product.note}</p>}
        <div className={styles.cardFooter}>
          <div>
            <span className={styles.price}>{currencyCny.format(product.landed_cny)}</span>
            <span className={styles.priceNote}>中国大陆到手价（估算）</span>
          </div>
          {productUrl && (
            <a
              className={styles.externalLink}
              href={productUrl}
              target="_blank"
              rel="noreferrer"
              aria-label={
                searchLink
                  ? `在 ${marketplace} 搜索 ${product.title}`
                  : `前往 ${marketplace} 查看 ${product.title}`
              }
              title={searchLink ? "打开商城搜索结果" : "打开具体商品详情"}
            >
              <span>{searchLink ? "平台搜索" : "查看商品"}</span>
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
              <th scope="col">商品价（原币）</th>
              <th scope="col">商品价（CNY）</th>
              <th scope="col">运费估算</th>
              <th scope="col">关税估算</th>
              <th scope="col">到手价（CNY）</th>
              <th scope="col">时效估算</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.platform}-${row.item_id}`}>
                <th scope="row">{row.title}</th>
                <td>{row.platform.toUpperCase()}</td>
                <td>{providerSourceLabel(row.source)}</td>
                <td>{row.currency} {row.price.toFixed(2)}</td>
                <td>{currencyCny.format(row.price_cny)}</td>
                <td>
                  {row.shipping_cny == null ? "待确认" : `${currencyCny.format(row.shipping_cny)}（估算）`}
                </td>
                <td>{row.duty_cny == null ? "待确认" : `${currencyCny.format(row.duty_cny)}（估算）`}</td>
                <td className={styles.tablePrice}>{currencyCny.format(row.landed_cny ?? row.price_cny)}</td>
                <td>{row.eta_days == null ? "待确认" : `${row.eta_days} 天（估算）`}</td>
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
                <dt>商品价（原币）</dt>
                <dd>{row.currency} {row.price.toFixed(2)}</dd>
              </div>
              <div>
                <dt>商品价（CNY）</dt>
                <dd>{currencyCny.format(row.price_cny)}</dd>
              </div>
              <div>
                <dt>运费估算</dt>
                <dd>{row.shipping_cny == null ? "待确认" : currencyCny.format(row.shipping_cny)}</dd>
              </div>
              <div>
                <dt>关税估算</dt>
                <dd>{row.duty_cny == null ? "待确认" : currencyCny.format(row.duty_cny)}</dd>
              </div>
              <div>
                <dt>到手价（CNY）</dt>
                <dd>{currencyCny.format(row.landed_cny ?? row.price_cny)}</dd>
              </div>
              <div>
                <dt>时效估算</dt>
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
  const unavailable = state.result?.unavailable_marketplaces ?? [];
  return (
    <section className={styles.providerDisclosure} aria-labelledby="provider-heading">
      <div>
        <h3 id="provider-heading">平台覆盖</h3>
        <span>
          {providers.length
            ? `${providers.length - unavailable.length}/${providers.length} 个平台返回结果`
            : "本次结果未返回提供方明细"}
        </span>
      </div>
      {providers.length > 0 && (
        <ul>
          {providers.map(([name, metadata]) => (
            <li key={name} data-status={metadata.status}>
              <strong>{providerNameLabel(name)}</strong>
              <span>
                {providerSourceLabel(metadata.source)} · {providerStatusLabel(metadata.status)} · {metadata.provider}
              </span>
              {metadata.failure_reason && <small>{providerReasonLabel(metadata.failure_reason)}</small>}
              {metadata.fallback_reason && <small>{providerReasonLabel(metadata.fallback_reason)}</small>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RecallDisclosure({ state }: { state: AgentState }) {
  const provenance = state.result?.recall_provenance;
  if (!provenance) return null;
  const channels = Object.entries(provenance.channels);
  return (
    <section className={styles.providerDisclosure} aria-labelledby="recall-heading">
      <div>
        <h3 id="recall-heading">候选召回</h3>
        <span>
          {recallModeLabel(provenance.mode)} · {provenance.selected_candidate_count}/
          {provenance.input_candidate_count} 个 Product Evidence 候选进入成本与排序路径
        </span>
      </div>
      <ul>
        {channels.map(([name, channel]) => (
          <li key={name} data-status={channel.state === "configured" ? "degraded" : channel.state}>
            <strong>{recallChannelLabel(name)}</strong>
            <span>
              {recallStateLabel(channel.state)}
              {channel.participated ? " · 已参与" : " · 未参与"}
            </span>
            {(channel.state !== "ready" || channel.reason_code !== "ready") && (
              <small>{channel.reason_code}：{channel.reason}</small>
            )}
          </li>
        ))}
        {provenance.personalization && (
          <li
            data-status={
              provenance.personalization.state === "ready" && provenance.personalization.participated
                ? "ready"
                : provenance.personalization.state
            }
          >
            <strong>
              个性化召回 · {provenance.personalization.state === "ready" && provenance.personalization.participated
                ? "已生效"
                : provenance.personalization.state === "degraded"
                  ? "已降级"
                  : "未生效"}
            </strong>
            <span>
              输入来源：{personalizationInputSourceLabel(provenance.personalization.input_source)} · {personalizationSignalLabel(provenance.personalization.signal)}
            </span>
            <small>
              {provenance.personalization.reason_code}：{provenance.personalization.reason}
              {provenance.personalization.preference_values.length > 0
                ? `；字段值：${provenance.personalization.preference_values.join("、")}`
                : ""}
            </small>
          </li>
        )}
      </ul>
      {provenance.fallback_reason && <p>{`降级原因：${provenance.fallback_reason}`}</p>}
    </section>
  );
}

function IdentityEvidenceLine({ evidence }: { evidence: IdentityEvidence | null | undefined }) {
  if (!evidence) return null;
  const details = [
    evidence.matched_fields.length
      ? `已匹配：${evidence.matched_fields.join("、")}`
      : null,
    evidence.missing_fields.length
      ? `缺少：${evidence.missing_fields.join("、")}`
      : null,
    evidence.conflicting_fields.length
      ? `冲突：${evidence.conflicting_fields.join("、")}`
      : null,
  ].filter(Boolean);
  return (
    <div className={styles.identityEvidenceLine} data-decision={evidence.decision}>
      <strong>{identityEvidenceLabel(evidence)}</strong>
      <span>{evidence.explanation}</span>
      {details.length > 0 && <small>{details.join("；")}</small>}
    </div>
  );
}

function MatchingOffers({ state }: { state: AgentState }) {
  const result = state.result;
  if (!result || result.mode !== "exact_offer_comparison") return null;
  const offers = result.matching_offers ?? [];
  return (
    <section className={styles.decisionSection} aria-labelledby="matching-heading">
      <div className={styles.decisionSectionHeader}>
        <h3 id="matching-heading">Matching Offer</h3>
        <span>{offers.length ? `${offers.length} 个已证明同款` : "暂无已证明同款"}</span>
      </div>
      {offers.length ? (
        <ul className={styles.identityList}>
          {offers.map((offer) => (
            <li key={`${offer.platform}-${offer.item_id}`}>
              <div className={styles.decisionItemHeader}>
                <strong>{offer.title}</strong>
                <span>{offer.platform.toUpperCase()}</span>
              </div>
              <IdentityEvidenceLine evidence={offer.identity_evidence} />
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.identityEmpty}>
          没有可由跨平台 identifier 或全部关键属性证明为同一 Product Variant 的 offer。
        </p>
      )}
    </section>
  );
}

function AlternativeCandidates({ candidates }: { candidates: AlternativeCandidate[] }) {
  if (!candidates.length) return null;
  return (
    <section className={styles.decisionSection} aria-labelledby="alternative-heading">
      <div className={styles.decisionSectionHeader}>
        <h3 id="alternative-heading">Alternative Candidate</h3>
        <span>Identity Evidence 不足，不参与正式排名</span>
      </div>
      <ul className={styles.identityList}>
        {candidates.map((candidate) => (
          <li key={`${candidate.platform}-${candidate.item_id}`}>
            <div className={styles.decisionItemHeader}>
              <strong>{candidate.title}</strong>
              <span>{candidate.platform.toUpperCase()} · ¥{candidate.landed_cny.toFixed(0)}</span>
            </div>
            <p>{candidate.reason}</p>
            <IdentityEvidenceLine evidence={candidate.identity_evidence} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function EvaluationLine({ evaluation }: { evaluation: ConstraintEvaluation }) {
  return (
    <li data-status={evaluation.status}>
      <strong>{evaluation.constraint.label}</strong>
      <span>{evaluation.explanation}</span>
      <code>{evaluation.reason_code}</code>
    </li>
  );
}

function WorkingAssumptions({ assumptions }: { assumptions: WorkingAssumption[] }) {
  if (!assumptions.length) return null;
  return (
    <section className={styles.decisionSection} aria-labelledby="assumption-heading">
      <div className={styles.decisionSectionHeader}>
        <h3 id="assumption-heading">工作假设</h3>
        <span>可见默认，不是硬性条件</span>
      </div>
        <ul className={styles.assumptionList}>
          {assumptions.map((assumption) => (
            <li key={assumption.code}>
            <strong>{evidenceLabels[assumption.field] ?? assumption.field}：{assumption.value}</strong>
              <span>{assumption.reason}</span>
            </li>
          ))}
      </ul>
    </section>
  );
}

function UnverifiedCandidates({ candidates }: { candidates: UnverifiedCandidate[] }) {
  if (!candidates.length) return null;
  return (
    <section className={styles.decisionSection} aria-labelledby="unverified-heading">
      <div className={styles.decisionSectionHeader}>
        <h3 id="unverified-heading">未验证候选</h3>
        <span>缺证据，不参与正式推荐</span>
      </div>
      <ul className={styles.decisionList}>
        {candidates.map((candidate) => (
          <li key={`${candidate.platform}-${candidate.item_id}`}>
            <div className={styles.decisionItemHeader}>
              <strong>{candidate.title}</strong>
              <span>{candidate.platform.toUpperCase()} · ¥{candidate.landed_cny.toFixed(0)}</span>
            </div>
            <p>{candidate.reason}</p>
            <ul className={styles.evaluationList}>
              {candidate.constraint_evaluations
                .filter((evaluation) => evaluation.status === "unknown")
                .map((evaluation) => (
                  <EvaluationLine
                    key={`${candidate.item_id}-${evaluation.constraint.id}`}
                    evaluation={evaluation}
                  />
                ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Exclusions({ exclusions }: { exclusions: ConstraintExclusion[] }) {
  if (!exclusions.length) return null;
  return (
    <section className={styles.decisionSection} aria-labelledby="exclusion-heading">
      <div className={styles.decisionSectionHeader}>
        <h3 id="exclusion-heading">排除原因</h3>
        <span>{exclusions.length} 个候选未满足硬性条件</span>
      </div>
      <ul className={styles.decisionList}>
        {exclusions.map((exclusion) => (
          <li key={`${exclusion.platform}-${exclusion.item_id}`}>
            <div className={styles.decisionItemHeader}>
              <strong>{exclusion.title}</strong>
              <span>{exclusion.violated_count} 项违反</span>
            </div>
            <ul className={styles.evaluationList}>
              {exclusion.violated_constraints.map((evaluation) => (
                <EvaluationLine
                  key={`${exclusion.item_id}-${evaluation.constraint.id}`}
                  evaluation={evaluation}
                />
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CalculationExclusions({ exclusions }: { exclusions: CalculationExclusion[] }) {
  if (!exclusions.length) return null;
  return (
    <section className={styles.decisionSection} aria-labelledby="calculation-exclusion-heading">
      <div className={styles.decisionSectionHeader}>
        <h3 id="calculation-exclusion-heading">计算排除</h3>
        <span>{exclusions.length} 个候选未参与计算或排序</span>
      </div>
      <ul className={styles.decisionList}>
        {exclusions.map((exclusion) => (
          <li key={`${exclusion.platform}-${exclusion.item_id}`}>
            <div className={styles.decisionItemHeader}>
              <strong>{exclusion.title}</strong>
              <span>
                {exclusion.amount == null
                  ? `${exclusion.currency} 原始金额不可用`
                  : `${exclusion.currency} ${exclusion.amount.toFixed(2)}`}
              </span>
            </div>
            <p>{exclusion.reason}</p>
            <code>{exclusion.reason_code}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}

function resultHasEvidence(result: NonNullable<AgentState["result"]>): boolean {
  return (
    result.product_evidence.length > 0 ||
    result.recommendations.length > 0 ||
    result.comparison.length > 0 ||
    result.matching_offers.length > 0 ||
    result.alternative_candidates.length > 0 ||
    (result.unverified_candidates?.length ?? 0) > 0 ||
    (result.exclusions?.length ?? 0) > 0
  );
}

const preferenceStatusLabels: Record<PreferenceDecision["status"], string> = {
  applied: "应用",
  ignored: "忽略",
  overridden: "覆盖",
};

const preferenceSourceLabels: Record<PreferenceDecision["source"], string> = {
  current_request: "当前请求",
  remembered_preference: "Remembered Preference",
};

function PreferenceRationale({ decisions }: { decisions: PreferenceDecision[] }) {
  return (
    <section className={styles.decisionSection} aria-labelledby="preference-heading">
      <div className={styles.decisionSectionHeader}>
        <h3 id="preference-heading">偏好处理</h3>
        <span>仅作为 eligible candidate 的透明 ranking 输入</span>
      </div>
      {decisions.length ? (
        <ul className={styles.preferenceDecisionList}>
          {decisions.map((decision, index) => (
            <li key={`${decision.field}-${decision.value}-${decision.source}-${index}`} data-status={decision.status}>
              <strong>{`${preferenceStatusLabels[decision.status]}：${decision.value}`}</strong>
              <span>来源：{preferenceSourceLabels[decision.source]}</span>
              <small>{decision.reason}</small>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.identityEmpty}>本任务没有可应用的 Remembered Preference 或显式软偏好。</p>
      )}
    </section>
  );
}

function DecisionTransparency({ state, onRelax }: { state: AgentState; onRelax?: (constraintId: string) => void }) {
  const result = state.result;
  if (!result) return null;
  const assumptions = result.working_assumptions ?? [];
  const unverified = result.unverified_candidates ?? [];
  const exclusions = result.exclusions ?? [];
  const calculationExclusions = result.calculation_exclusions ?? [];
  const suggestions = result.relaxation_suggestions ?? [];
  const isEmpty = !resultHasEvidence(result);
  const isNoMatch = !isEmpty && (result.match_status === "no_match" || result.recommendations.length === 0);
  const hasExactIdentityMatch = result.matching_offers.length > 0;
  const exactIdentityNoMatch = result.mode === "exact_offer_comparison" && !hasExactIdentityMatch;

  return (
    <div className={styles.decisionTransparency}>
      {isEmpty && (
        <section className={styles.emptyResult} role="status" aria-label="空结果">
          <strong>没有可用的 Product Evidence</strong>
          <span>本次平台查询没有返回可用于筛选、成本核算或排序的候选。</span>
        </section>
      )}
      {isNoMatch && (
        <section className={styles.noMatch} role="status" aria-label="无匹配结果">
          <strong>
            {exactIdentityNoMatch
              ? "没有 Identity Evidence 充分的 Matching Offer"
              : "没有满足全部硬性条件的候选"}
          </strong>
          <span>
            {exactIdentityNoMatch
              ? "这是成功的 No-Match Result；相似商品已列为 Alternative Candidate，不参与正式排名。"
              : "这是成功的 No-Match Result；平台数据可用，但没有证据充分且满足约束的推荐。"}
          </span>
        </section>
      )}
      <PreferenceRationale decisions={result.preference_decisions ?? []} />
      <CalculationExclusions exclusions={calculationExclusions} />
      <MatchingOffers state={state} />
      <AlternativeCandidates candidates={result.alternative_candidates ?? []} />
      <WorkingAssumptions assumptions={assumptions} />
      <UnverifiedCandidates candidates={unverified} />
      <Exclusions exclusions={exclusions} />
      {suggestions.length > 0 && (
        <section className={styles.decisionSection} aria-labelledby="relaxation-heading">
          <div className={styles.decisionSectionHeader}>
            <h3 id="relaxation-heading">约束放宽建议</h3>
            <span>需你确认后才会开始新任务</span>
          </div>
          <ul className={styles.assumptionList}>
            {suggestions.map((suggestion) => (
              <li key={suggestion.constraint.id}>
                <strong>{suggestion.constraint.label}</strong>
                <span>{suggestion.suggestion}</span>
                {onRelax && (
                  <button type="button" onClick={() => onRelax(suggestion.constraint.id)}>
                    确认放宽并开始新研究
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function ResultDisclosure({ state }: { state: AgentState }) {
  const result = state.result;
  if (!result) return null;
  if (result.data_mode === "mixed") {
    return (
      <p className={styles.resultDisclosure} role="note">
        仅开发诊断模式允许混合来源；此结果不是普通用户可发起的 Live Result 或 Sandbox Result。
      </p>
    );
  }
  if (result.result_kind === "partial") {
    const unavailable = result.unavailable_marketplaces.map(providerNameLabel).join("、");
    return (
      <p className={styles.resultDisclosure} role="note">
        已返回可用平台的 Product Evidence；{unavailable || "部分平台"}不可用，稳定失败原因见平台覆盖。
      </p>
    );
  }
  return (
    <p className={styles.resultDisclosure} role="note">
      {result.data_mode === "sandbox"
        ? "本次结果仅来自显式启用的 Sandbox Result fixture。"
        : "本次结果仅来自已配置数据提供商通道网关的 Live Result。"}
    </p>
  );
}

function RankingDisclosure({ profile }: { profile: RankingProfile | undefined }) {
  const active = profile ?? defaultRankingProfile;
  const rankingOrder = active.priority_order.map((dimension) => rankingLabels[dimension]).join(" > ");
  return (
    <p className={styles.rankingNotice} role="note">
      <ListOrdered size={16} aria-hidden="true" />
      <span>
        <strong>排序依据：{rankingOrder}</strong>
        <small>{active.explicit ? "当前请求已表达优先级" : "当前请求未表达优先级，默认以到手价优先"}</small>
      </span>
    </p>
  );
}

export default function ResearchContent({
  state,
  view,
  onViewChange,
  onUseStarter,
  onReset,
  onRerun,
  onRelax,
}: ResearchContentProps) {
  if (state.status === "idle") return <StarterState onUseStarter={onUseStarter} />;
  if (["starting", "connecting", "running", "awaiting_clarification"].includes(state.status)) {
    return <WaitingState eventCount={state.events.length} />;
  }
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
            {resultBadgeLabel(
              result?.data_mode ?? result?.provider_mode ?? "live",
              result?.result_kind ?? "live",
            )}
          </span>
          <span className={styles.modeBadge}>{researchModeLabel(result?.mode)}</span>
          <h2 id="result-heading">购物建议</h2>
        </div>
        <div className={styles.resultActions}>
          {onRerun && (
            <button className={styles.rerunButton} type="button" onClick={onRerun}>
              <RefreshCw size={15} aria-hidden="true" />
              Research Rerun
            </button>
          )}
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
      </div>

      {state.snapshot?.lineage && (
        <p className={styles.lineageNotice} role="status">
          {state.snapshot.lineage.relation === "constraint_relaxation"
            ? "本次研究来自已确认的 Constraint Relaxation"
            : "本次研究是 Research Rerun"}
          {` · parent snapshot ${state.snapshot.lineage.parent_snapshot_id}`}
        </p>
      )}

      {result?.final_answer && <p className={styles.summary}>{result.final_answer}</p>}
      <ResultDisclosure state={state} />
      {result?.calculation_notice && (
        <p className={styles.calculationNotice}>
          <Calculator size={16} aria-hidden="true" />
          <span>{result.calculation_notice}</span>
        </p>
      )}
      <p className={styles.calculationDisclaimer} role="note">
        运费、关税和配送时效均为估算；这不是 checkout guarantee。
      </p>
      <RankingDisclosure profile={result?.ranking_profile} />
      <ProviderDisclosure state={state} />
      <RecallDisclosure state={state} />
      <DecisionTransparency state={state} onRelax={onRelax} />

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
            <p className={styles.noComparison}>
              {result && !resultHasEvidence(result)
                ? "研究已完成，但平台没有返回可用的 Product Evidence。"
                : result?.mode === "exact_offer_comparison" && result.matching_offers.length === 0
                  ? "研究已完成，但没有 Identity Evidence 充分的 Matching Offer。"
                  : "研究已完成，但没有商品同时满足硬性条件。"}
            </p>
          )
        ) : (
          <Comparison state={state} />
        )}
      </div>

      {result?.files.length ? <ReportDownloads files={result.files} /> : null}
    </section>
  );
}
