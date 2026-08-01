// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initialAgentState } from "../hooks/useShoppingAgent";
import type {
  CalculationExclusion,
  ConstraintExclusion,
  HardConstraint,
  ProviderMetadata,
  Recommendation,
  TaskResultData,
  UnverifiedCandidate,
  WorkingAssumption,
} from "../types/api";
import ResearchContent from "./ResearchContent";

afterEach(cleanup);

const evidenceRecommendation: Recommendation = {
  item_id: "candidate-one",
  platform: "amazon",
  marketplace: "amazon",
  offer_id: "offer-with-a-very-long-identifier-1234567890",
  title: "Acme X1 headphones",
  image_url: null,
  product_url: "https://shop.example/search?q=acme+x1",
  link_kind: "marketplace_search",
  price: 99,
  currency: "USD",
  price_cny: 710.82,
  shipping_cny: 85,
  duty_cny: 92.41,
  landed_cny: 888.23,
  eta_days: 12,
  rating: null,
  sales: null,
  attributes: {},
  identity: {
    gtin: "4006381333931",
    mpn: "ACME-X1",
    brand: "Acme",
    model: "X1",
  },
  variant_attributes: { capacity: "256 GB", condition: "new" },
  availability: "in_stock",
  retrieved_at: "2026-07-30T10:00:00Z",
  provenance: {
    kind: "marketplace_gateway",
    provider: "licensed-amazon-feed",
    upstream_source: "amazon-catalog",
  },
  source: "live",
  note: null,
  duty_tier: "标准",
  shipping_estimate: {
    estimated: true,
    source: "shipping_rules",
    calculation_basis: "平台和重量区间",
  },
  duty_estimate: {
    estimated: true,
    source: "duty_rules",
    calculation_basis: "商品价 CNY × 平台关税率",
  },
  delivery_estimate: {
    estimated: true,
    source: "shipping_rules",
    calculation_basis: "平台和重量区间",
  },
  reason: "到手价与证据完整",
  rank: 1,
  constraint_evaluations: [],
  score_breakdown: {
    priority_order: ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
    landed_cost_cny: 888.23,
    landed_cost_score: 1,
    preference_match_score: 0.5,
    evidence_quality_score: 0.8,
    delivery_time_days: 12,
    delivery_time_score: 1,
  },
};

function renderCompletedResult(
  recommendation: Recommendation | null = evidenceRecommendation,
  resultOverrides: Partial<TaskResultData> = {},
) {
  render(
    <ResearchContent
      state={{
        ...initialAgentState,
        status: "completed",
        result: {
          thread_id: "thread-evidence",
          final_answer: "已按可核验证据整理结果。",
          recommendations: recommendation ? [recommendation] : [],
          comparison: [],
          files: [],
          provider_mode: "live",
          providers: {},
          calculation_notice: "价格为抓取时点信息。",
          exchange_rate: {
            base_currency: "CNY",
            source: "reference-table",
            effective_date: "2026-01-01",
            calculation_basis: "original_amount * rate_to_cny",
          },
          calculation_exclusions: [],
          ranking_profile: {
            priority_order: ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
            explicit: false,
          },
          data_mode: "live",
          result_kind: "live",
          unavailable_marketplaces: [],
          ...resultOverrides,
        },
      }}
      view="recommendations"
      onViewChange={vi.fn()}
      onUseStarter={vi.fn()}
      onReset={vi.fn()}
    />,
  );
}

describe("Product Evidence", () => {
  it("renders typed link semantics and verifiable offer evidence", () => {
    renderCompletedResult();

    expect(screen.getByText(/实时商品.*licensed-amazon-feed/)).toBeTruthy();
    expect(screen.getByText("有货")).toBeTruthy();
    expect(screen.getByText("4006381333931")).toBeTruthy();
    expect(screen.getByText("ACME-X1")).toBeTruthy();
    expect(screen.getByText("256 GB")).toBeTruthy();
    expect(screen.getByText("上游来源")).toBeTruthy();
    expect(screen.getByText("amazon-catalog")).toBeTruthy();
    const retrievedAt = document.querySelector('time[datetime="2026-07-30T10:00:00Z"]');
    expect(retrievedAt?.textContent).toContain("2026");

    const link = screen.getByRole("link", { name: /在 Amazon 搜索 Acme X1 headphones/ });
    expect(link.textContent).toContain("平台搜索");
    expect(link.getAttribute("href")).toBe("https://shop.example/search?q=acme+x1");
    link.focus();
    expect(document.activeElement).toBe(link);
  });

  it("labels Product Detail Links separately from marketplace search", () => {
    renderCompletedResult({
      ...evidenceRecommendation,
      product_url: "https://shop.example/offers/offer-1",
      link_kind: "product_detail",
    });

    const link = screen.getByRole("link", { name: /前往 Amazon 查看 Acme X1 headphones/ });
    expect(link.textContent).toContain("查看商品");
    expect(link.getAttribute("href")).toBe("https://shop.example/offers/offer-1");
  });

  it("shows the landed-cost breakdown, estimate sources, and ranking basis", () => {
    renderCompletedResult();

    expect(screen.getByText("USD 99.00")).toBeTruthy();
    expect(screen.getByText("¥710.82")).toBeTruthy();
    expect(screen.getByText("¥85.00")).toBeTruthy();
    expect(screen.getByText("¥92.41")).toBeTruthy();
    expect(screen.getByText("运费 估算")).toBeTruthy();
    expect(screen.getByText("关税 估算")).toBeTruthy();
    expect(screen.getByText("中国大陆到手价（估算）")).toBeTruthy();
    expect(screen.getByText("来源：shipping_rules")).toBeTruthy();
    expect(screen.getByText(/排序依据：到手价/)).toBeTruthy();
    expect(screen.getByText(/checkout guarantee/)).toBeTruthy();
  });

  it("shows currency and amount exclusions with machine-readable reasons", () => {
    const exclusions: CalculationExclusion[] = [
      {
        item_id: "unsupported-hkd",
        platform: "ebay",
        title: "港币耳机",
        currency: "HKD",
        amount: 100,
        reason_code: "unsupported_currency",
        reason: "没有可用的 HKD 到 CNY 汇率，已排除计算和排序。",
      },
      {
        item_id: "invalid-price",
        platform: "amazon",
        title: "非法金额耳机",
        currency: "USD",
        amount: null,
        reason_code: "invalid_amount",
        reason: "商品原始金额不是有限的非负数，已排除计算和排序。",
      },
    ];
    renderCompletedResult(evidenceRecommendation, { calculation_exclusions: exclusions });

    expect(screen.getByRole("heading", { name: "计算排除" })).toBeTruthy();
    expect(screen.getByText("HKD 100.00")).toBeTruthy();
    expect(screen.getByText("USD 原始金额不可用")).toBeTruthy();
    expect(screen.getByText("unsupported_currency")).toBeTruthy();
    expect(screen.getByText("invalid_amount")).toBeTruthy();
  });

  it("omits unsafe links and keeps missing evidence explicit", () => {
    renderCompletedResult({
      ...evidenceRecommendation,
      offer_id: null,
      identity: { gtin: null, mpn: null, brand: null, model: null },
      variant_attributes: {},
      availability: null,
      retrieved_at: null,
      product_url: "javascript:alert(1)",
      link_kind: "product_detail",
    });

    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("跨平台标识")).toBeTruthy();
    expect(screen.getAllByText("未提供").length).toBeGreaterThanOrEqual(4);
  });

  it("labels a partial result and discloses failed marketplace coverage", () => {
    const failedProvider: ProviderMetadata = {
      source: "live",
      provider: "ebay-feed",
      status: "unavailable",
      fallback_reason: "provider request failed: TimeoutException",
      failure_reason: "request_failed",
    };
    renderCompletedResult(evidenceRecommendation, {
      result_kind: "partial",
      unavailable_marketplaces: ["ebay"],
      providers: {
        amazon: {
          source: "live",
          provider: "amazon-feed",
          status: "ok",
          fallback_reason: null,
          failure_reason: null,
        },
        ebay: failedProvider,
      },
    });

    expect(screen.getByText("Partial Result")).toBeTruthy();
    expect(screen.getByText(/eBay.*不可用/)).toBeTruthy();
    expect(screen.getByText("平台网关请求失败")).toBeTruthy();
    expect(screen.getByText("平台请求失败（TimeoutException）")).toBeTruthy();
  });

  it("separates no-match, unverified candidates, exclusions, and assumptions", () => {
    const materialConstraint: HardConstraint = {
      id: "material_not_contains_plastic",
      kind: "material",
      field: "material",
      operator: "not_contains",
      value: "塑料",
      unit: null,
      label: "材质不含塑料",
    };
    const unverified: UnverifiedCandidate = {
      ...evidenceRecommendation,
      reason: "缺少可验证证据：材质不含塑料",
      constraint_evaluations: [
        {
          constraint: materialConstraint,
          status: "unknown",
          reason_code: "missing_product_evidence",
          explanation: "无法从 Product Evidence 验证材质不含塑料。",
          evidence: [],
        },
      ],
    };
    const exclusion: ConstraintExclusion = {
      item_id: "excluded-one",
      platform: "amazon",
      title: "塑料耳机",
      violated_count: 1,
      violated_constraints: [
        {
          constraint: materialConstraint,
          status: "violated",
          reason_code: "prohibited_attribute_present",
          explanation: "材质不含塑料，证据为 attributes.material=塑料。",
          evidence: [
            { field_path: "attributes.material", value: "塑料", source: "product_evidence" },
          ],
        },
      ],
    };
    const assumptions: WorkingAssumption[] = [
      {
        code: "optional_color_unspecified",
        field: "color",
        value: "不设限",
        reason: "请求未指定颜色。",
      },
    ];

    renderCompletedResult(null, {
      recommendations: [],
      match_status: "no_match",
      unverified_candidates: [unverified],
      exclusions: [exclusion],
      working_assumptions: assumptions,
      relaxation_suggestions: [
        {
          constraint: materialConstraint,
          suggestion: "当前任务未自动放宽。",
          requires_confirmation: true,
        },
      ],
    });

    expect(screen.getByRole("status", { name: "无匹配结果" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "未验证候选" })).toBeTruthy();
    expect(screen.getByText("缺少可验证证据：材质不含塑料")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "排除原因" })).toBeTruthy();
    expect(screen.getByText("塑料耳机")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "工作假设" })).toBeTruthy();
    expect(screen.getByText((_, element) => element?.textContent === "颜色：不设限")).toBeTruthy();
    expect(screen.getByText("当前任务未自动放宽。")).toBeTruthy();
  });
});
