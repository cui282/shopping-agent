// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initialAgentState } from "../hooks/useShoppingAgent";
import type {
  AlternativeCandidate,
  CalculationExclusion,
  ConstraintExclusion,
  GeneratedFile,
  HardConstraint,
  IdentityEvidence,
  ProviderMetadata,
  RecallChannelName,
  RecallChannelReport,
  Recommendation,
  ShippingCalculationExclusion,
  TaskResultData,
  TaxCalculationExclusion,
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
  insurance_cny: 0,
  duty_cny: null,
  import_vat_cny: null,
  consumption_tax_cny: null,
  import_tax_cny: 92.41,
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
  price_conversion: {
    source_currency: "USD",
    target_currency: "CNY",
    rate_to_cny: 7.18,
    purpose: "comparison_estimate",
    rate_type: "provider_quote",
    markup_status: "excluded",
    markup_bps: null,
    provider: "licensed-fx-feed",
    source_reference: "fx-quote-20260811-001",
    observed_at: "2026-08-11T01:30:00Z",
    expires_at: "2026-08-12T01:30:00Z",
  },
  shipping_quote: {
    quote_type: "carrier_quote",
    currency: "CNY",
    total_amount: 85,
    base_amount: 75,
    surcharge_amount: 10,
    discount_amount: 0,
    actual_weight_kg: 0.42,
    dimensional_weight_kg: 0.58,
    chargeable_weight_kg: 0.58,
    length_cm: 24,
    width_cm: 18,
    height_cm: 12,
    dimensional_divisor: 6000,
    origin_country: "US",
    destination_country: "CN",
    service_name: "Tracked Air",
    eta_min_days: 8,
    eta_max_days: 12,
    provider: "licensed-logistics-feed",
    source_reference: "shipping-quote-20260811-001",
    observed_at: "2026-08-11T01:31:00Z",
    expires_at: "2026-08-12T01:31:00Z",
    currency_conversion: null,
  },
  customs: {
    hs_code: "8518300000",
    country_of_origin: "CN",
    destination_country: "CN",
    ship_from_country: "US",
    import_regime: "seller_collected",
    rate_type: "provider_quote",
    tariff_rate: null,
    import_vat_rate: null,
    consumption_tax_rate: 0,
    personal_postal_tax_rate: null,
    personal_postal_assessed_value_cny: null,
    personal_postal_total_value_cny: null,
    personal_postal_value_limit_cny: null,
    personal_postal_tax_exemption_threshold_cny: null,
    personal_postal_single_indivisible_item: null,
    personal_postal_eligible: null,
    seller_collected_tax_cny: 92.41,
    insurance_cny: 0,
    valuation: null,
    cross_border_ecommerce_eligible: null,
    provider: "licensed-customs-feed",
    source_reference: "checkout tax quote q-123",
    effective_date: "2026-08-11",
  },
  note: null,
  duty_tier: "标准",
  shipping_estimate: {
    estimated: true,
    source: "licensed-logistics-feed",
    calculation_basis: "Tracked Air 线路报价；计费重量 0.58kg；含附加费 CNY 10.00",
  },
  duty_estimate: {
    estimated: true,
    source: "duty_rules",
    calculation_basis: "商品价 CNY × 平台关税率",
  },
  tax_estimate: {
    estimated: true,
    source: "licensed-customs-feed",
    calculation_basis: "使用数据通道返回的卖家/结算页已代收进口税费报价，不拆分税种",
  },
  delivery_estimate: {
    estimated: true,
    source: "licensed-logistics-feed",
    calculation_basis: "Tracked Air 报价时效 8-12 天；排序采用上限",
  },
  tax_breakdown: {
    import_regime: "seller_collected",
    calculation_method: "provider_quote",
    hs_code: "8518300000",
    country_of_origin: "CN",
    destination_country: "CN",
    customs_value_cny: 795.82,
    customs_valuation: null,
    rate_type: "provider_quote",
    tariff_rate: null,
    import_vat_rate: null,
    consumption_tax_rate: 0,
    personal_postal_tax_rate: null,
    personal_postal_assessed_value_cny: null,
    personal_postal_total_value_cny: null,
    personal_postal_value_limit_cny: null,
    personal_postal_tax_exemption_threshold_cny: null,
    personal_postal_single_indivisible_item: null,
    policy_factor: 1,
    tariff_cny: null,
    import_vat_cny: null,
    consumption_tax_cny: null,
    tax_before_exemption_cny: null,
    tax_exemption_cny: 0,
    tax_exemption_reason: null,
    total_import_tax_cny: 92.41,
    provider: "licensed-customs-feed",
    source_reference: "checkout tax quote q-123",
    effective_date: "2026-08-11",
    calculation_basis: "使用数据通道返回的卖家/结算页已代收进口税费报价，不拆分税种",
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
  offer_kind: "research_candidate",
  identity_evidence: {
    decision: "not_required",
    basis: "not_required",
    matched_fields: [],
    missing_fields: [],
    conflicting_fields: [],
    explanation: "不同商品推荐不要求跨平台同款证明。",
  },
};

const alternativeEvidence: IdentityEvidence = {
  decision: "alternative_candidate",
  basis: "insufficient",
  matched_fields: [],
  missing_fields: ["identity.gtin", "identity.mpn", "identity.brand", "identity.model"],
  conflicting_fields: [],
  explanation: "只有标题相似，缺少可验证的跨平台身份或完整规格证据。",
};

const alternativeCandidate: AlternativeCandidate = {
  ...evidenceRecommendation,
  reason: "身份信息不足，保留为替代候选。",
  identity_evidence: alternativeEvidence,
};

const matchingOffer: Recommendation = {
  ...evidenceRecommendation,
  offer_kind: "matching_offer",
  identity_evidence: {
    decision: "matching_offer",
    basis: "identifier",
    matched_fields: ["identity.gtin"],
    missing_fields: [],
    conflicting_fields: [],
    explanation: "GTIN 跨平台一致，且没有发现冲突的关键属性。",
  },
};

function unavailableRecallChannel(channel: RecallChannelName): RecallChannelReport {
  return {
    channel,
    configured: false,
    state: "unavailable",
    reason_code: "not_configured",
    reason: "channel is not configured",
    participated: false,
  };
}

function renderCompletedResult(
  recommendation: Recommendation | null = evidenceRecommendation,
  resultOverrides: Partial<TaskResultData> = {},
  actions: {
    onRerun?: () => void;
    onRelax?: (constraintId: string) => void;
    onRememberPreference?: (value: string) => Promise<boolean>;
    view?: "recommendations" | "comparison";
  } = {},
) {
  render(
    <ResearchContent
      state={{
        ...initialAgentState,
        status: "completed",
        result: {
          thread_id: "thread-evidence",
          final_answer: "已按可核验证据整理结果。",
          resolved_query: null,
          resolved_intent: null,
          applied_preferences: {
            material_preferences: [],
            style_preferences: [],
            soft_preferences: [],
            avoid: [],
          },
          task_overrides: [],
          constraint_relaxations: [],
          product_evidence: [],
          mode: "product_research",
          recommendations: recommendation ? [recommendation] : [],
          comparison: [],
          matching_offers: [],
          alternative_candidates: [],
          files: [],
          provider_mode: "live",
          providers: {},
          calculation_notice: "价格为抓取时点信息。",
          exchange_rate: {
            base_currency: "CNY",
            source: "offer-level-quotes",
            effective_date: "2026-08-11T01:30:00Z",
            calculation_basis: "original_amount * rate_to_cny",
            providers: ["licensed-fx-feed"],
            quote_count: 1,
            settlement_notice: "最终支付汇率和支付机构费用以平台结算页及发卡行为准。",
          },
          calculation_exclusions: [],
          shipping_exclusions: [],
          tax_exclusions: [],
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
      view={actions.view ?? "recommendations"}
      onViewChange={vi.fn()}
      onUseStarter={vi.fn()}
      onReset={vi.fn()}
      onRerun={actions.onRerun}
      onRelax={actions.onRelax}
      onRememberPreference={actions.onRememberPreference}
    />,
  );
}

describe("Product Evidence", () => {
  it("shows the interpreted shopping requirements before the recommendations", () => {
    renderCompletedResult(evidenceRecommendation, {
      resolved_intent: {
        mode: "product_research",
        budget_cny: 1500,
        category: "通勤双肩包",
        material_preferences: ["防水尼龙"],
        style_preferences: ["简约"],
        hard_constraints: [
          {
            id: "fits_16_inch_laptop",
            kind: "specification",
            field: "laptop_size",
            operator: "gte",
            value: 16,
            unit: "inch",
            label: "可放 16 英寸电脑",
          },
        ],
        soft_preferences: ["轻量"],
        destination: "中国大陆",
        ranking_profile: {
          priority_order: ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
          explicit: false,
        },
        working_assumptions: [],
        source: "computed",
      },
    });

    const summary = screen.getByRole("region", { name: "已理解的需求" });
    expect(summary.textContent).toContain("通勤双肩包");
    expect(summary.textContent).toContain("预算不超过 ¥1,500");
    expect(summary.textContent).toContain("简约");
    expect(summary.textContent).toContain("可放 16 英寸电脑");
  });

  it("only remembers an inferred style after the customer confirms it", async () => {
    const onRememberPreference = vi.fn().mockResolvedValue(true);
    renderCompletedResult(
      evidenceRecommendation,
      {
        resolved_intent: {
          mode: "product_research",
          budget_cny: null,
          category: "通勤双肩包",
          material_preferences: [],
          style_preferences: ["简约"],
          hard_constraints: [],
          soft_preferences: [],
          destination: "中国大陆",
          ranking_profile: {
            priority_order: ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
            explicit: false,
          },
          working_assumptions: [],
          source: "computed",
        },
      },
      { onRememberPreference },
    );

    expect(onRememberPreference).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "以后也按“简约”推荐" }));

    await waitFor(() => expect(onRememberPreference).toHaveBeenCalledWith("简约"));
    expect(screen.getByText("已保存为未来研究偏好")).toBeTruthy();
  });

  it("explains a history loading failure without calling the research failed", () => {
    render(
      <ResearchContent
        state={{
          ...initialAgentState,
          threadId: "thread-history",
          query: "比较三款通勤双肩包",
          status: "completed",
          loadError: "无法连接购物研究服务",
        }}
        view="recommendations"
        onViewChange={vi.fn()}
        onUseStarter={vi.fn()}
        onReset={vi.fn()}
        onRetryLoad={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "暂时无法打开这份研究" })).toBeTruthy();
    expect(screen.getByText(/历史任务状态没有改变/)).toBeTruthy();
    expect(screen.queryByText("这次研究没有完成")).toBeNull();
  });

  it("leads with a purchase verdict and keeps evidence details expandable", () => {
    renderCompletedResult();

    const verdict = screen.getByRole("region", { name: "购买结论" });
    expect(verdict.textContent).toContain("Acme X1 headphones");
    expect(verdict.textContent).toContain("¥888.23");

    const evidenceSummary = screen.getByText("价格与证据明细");
    const details = evidenceSummary.closest("details");
    expect(details).toBeTruthy();
    expect(details?.open).toBe(false);
    expect(details?.textContent).toContain("上游来源");
  });

  it("provides a professional comparison overview with working sort controls", () => {
    const lowerPriced = {
      ...evidenceRecommendation,
      item_id: "candidate-two",
      platform: "ebay",
      marketplace: "ebay",
      title: "Acme X1 budget offer",
      price: 86,
      price_cny: 620,
      shipping_cny: 40,
      duty_cny: 20,
      landed_cny: 680,
      eta_days: 5,
    } satisfies Recommendation;
    renderCompletedResult(
      evidenceRecommendation,
      { comparison: [evidenceRecommendation, lowerPriced] },
      { view: "comparison" },
    );

    const overview = screen.getByRole("region", { name: "专业比价概览" });
    expect(overview.textContent).toContain("Acme X1 headphones");
    expect(overview.textContent).toContain("¥680.00");
    expect(overview.textContent).toContain("5 天");
    expect(overview.textContent).toContain("2/2");

    const lowestPrice = screen.getByRole("button", { name: "最低价格" });
    fireEvent.click(lowestPrice);
    expect(lowestPrice.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector("tbody tr")?.textContent).toContain("Acme X1 budget offer");

    const fastestDelivery = screen.getByRole("button", { name: "最快送达" });
    fireEvent.click(fastestDelivery);
    expect(fastestDelivery.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector("tbody tr")?.textContent).toContain("Acme X1 budget offer");
  });

  it("offers an explicit rerun command for a completed result", () => {
    const onRerun = vi.fn();
    renderCompletedResult(evidenceRecommendation, {}, { onRerun });

    fireEvent.click(screen.getByRole("button", { name: "重新研究" }));

    expect(onRerun).toHaveBeenCalledOnce();
  });

  it("offers customer reports while keeping the JSON artifact out of the customer UI", () => {
    const files: GeneratedFile[] = [
      {
        file_id: "thread-evidence:markdown",
        format: "markdown",
        name: "shopping-report.md",
        url: "/api/files/thread-evidence/shopping-report.md",
        content_type: "text/markdown; charset=utf-8",
      },
      {
        file_id: "thread-evidence:json",
        format: "json",
        name: "shopping-report.json",
        url: "/api/files/thread-evidence/shopping-report.json",
        content_type: "application/json; charset=utf-8",
      },
      {
        file_id: "thread-evidence:pdf",
        format: "pdf",
        name: "shopping-report.pdf",
        url: "/api/files/thread-evidence/shopping-report.pdf",
        content_type: "application/pdf",
      },
    ];
    renderCompletedResult(evidenceRecommendation, { files });

    expect(screen.getByRole("status", { name: "研究报告下载" }).textContent).toContain(
      "2 份客户报告已准备",
    );
    const markdown = screen.getByRole("link", { name: "下载 详细版 Markdown 报告" });
    const pdf = screen.getByRole("link", { name: "下载 精简版 PDF 报告" });
    expect(markdown.getAttribute("download")).toBe("shopping-report.md");
    expect(pdf.getAttribute("download")).toBe("shopping-report.pdf");
    expect(screen.queryByRole("link", { name: /JSON/ })).toBeNull();
    fireEvent.click(pdf);
    expect(screen.getByRole("status", { name: "研究报告下载" }).textContent).toContain("PDF");
    pdf.focus();
    expect(document.activeElement).toBe(pdf);
  });

  it("discloses an invalid report link instead of exposing an unsafe download", () => {
    renderCompletedResult(evidenceRecommendation, {
      files: [
        {
          file_id: "thread-evidence:markdown",
          format: "markdown",
          name: "shopping-report.md",
          url: "data:text/plain,unsafe",
          content_type: "text/markdown; charset=utf-8",
        },
      ],
    });

    expect(screen.getByRole("alert").textContent).toContain("详细版 Markdown 报告链接不可用");
  });

  it("renders typed link semantics and verifiable offer evidence", () => {
    renderCompletedResult();

    expect(screen.getByText(/数据提供商通道.*licensed-amazon-feed/)).toBeTruthy();
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
    expect(screen.getByText("进口税费 估算")).toBeTruthy();
    expect(screen.getByText("HS Code 8518300000")).toBeTruthy();
    expect(screen.getByText("原产地 CN")).toBeTruthy();
    expect(screen.getByText("卖家已代收")).toBeTruthy();
    expect(screen.getByText("税率生效日 2026-08-11")).toBeTruthy();
    expect(screen.getByText("中国大陆到手价（估算）")).toBeTruthy();
    expect(screen.getByText("来源：licensed-logistics-feed")).toBeTruthy();
    expect(screen.getByText(/排序依据：到手价/)).toBeTruthy();
    expect(screen.getByText(/最终支付汇率、换汇费用、运费、进口税费和配送时效/)).toBeTruthy();
  });

  it("shows the offer-level FX and route-specific shipping evidence", () => {
    renderCompletedResult();

    expect(screen.getByText("价格换算")).toBeTruthy();
    expect(screen.getByText(/1 USD = ¥7\.18.*licensed-fx-feed/)).toBeTruthy();
    expect(screen.getByText("运费报价")).toBeTruthy();
    expect(screen.getByText(/US → 中国大陆.*Tracked Air.*计费重量 0\.58 kg/)).toBeTruthy();
    expect(screen.getByText("licensed-logistics-feed")).toBeTruthy();
  });

  it("explains when route quotes are missing instead of presenting a landed-cost ranking", () => {
    const exclusion: ShippingCalculationExclusion = {
      item_id: "candidate-one",
      platform: "amazon",
      title: "Acme X1 headphones",
      reason_code: "missing_shipping_quote",
      reason: "数据通道未提供面向中国大陆、包含运输服务和时效的运费报价。",
    };

    renderCompletedResult(null, {
      product_evidence: [evidenceRecommendation],
      recommendations: [],
      comparison: [],
      matching_offers: [],
      shipping_exclusions: [exclusion],
      match_status: "no_match",
    });

    expect(screen.getByText("运费报价不足，暂不能计算到手价")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "运费报价待补充" })).toBeTruthy();
    expect(screen.getByText(exclusion.reason)).toBeTruthy();
  });

  it("explains a personal-postal tax exemption in customer language", () => {
    renderCompletedResult({
      ...evidenceRecommendation,
      import_tax_cny: 0,
      landed_cny: 795.82,
      tax_breakdown: {
        ...evidenceRecommendation.tax_breakdown!,
        import_regime: "personal_postal",
        calculation_method: "personal_postal_rate",
        rate_type: "personal_postal",
        personal_postal_tax_rate: 0.2,
        personal_postal_assessed_value_cny: 250,
        personal_postal_total_value_cny: 250,
        personal_postal_value_limit_cny: 2000,
        personal_postal_tax_exemption_threshold_cny: 50,
        personal_postal_single_indivisible_item: false,
        tax_before_exemption_cny: 50,
        tax_exemption_cny: 50,
        tax_exemption_reason: "个人寄递物品应征税额不超过 ¥50.00，免税放行",
        total_import_tax_cny: 0,
      },
    });

    expect(screen.getAllByText("个人寄递物品应征税额不超过 ¥50.00，免税放行")).toHaveLength(2);
    expect(screen.getByText(/减免前应征 ¥50.00/)).toBeTruthy();
    expect(screen.getByText(/本次减免 ¥50.00/)).toBeTruthy();
  });

  it("shows preference decisions with their source and disposition", () => {
    renderCompletedResult(null, {
      preference_decisions: [
        {
          field: "style_preferences",
          value: "简约",
          status: "applied",
          source: "current_request",
          reason: "当前请求明确表达，本任务优先采用。",
        },
        {
          field: "style_preferences",
          value: "复古",
          status: "overridden",
          source: "remembered_preference",
          reason: "当前请求存在冲突表达，已保存的偏好不覆盖本次研究。",
        },
      ],
    });

    expect(screen.getByRole("heading", { name: "偏好处理" })).toBeTruthy();
    expect(screen.getByText(/应用.*简约/)).toBeTruthy();
    expect(screen.getByText(/覆盖.*复古/)).toBeTruthy();
    expect(screen.getByText(/来源：当前请求/)).toBeTruthy();
    expect(screen.getByText(/来源：已保存的偏好/)).toBeTruthy();
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

  it("explains missing customs evidence without exposing the reason code by default", () => {
    const taxExclusions: TaxCalculationExclusion[] = [
      {
        item_id: "unclassified",
        platform: "ebay",
        title: "待归类商品",
        reason_code: "missing_customs_evidence",
        reason: "数据通道未提供 HS Code、原产地、进口模式及带生效日期的税率证据。",
      },
    ];
    renderCompletedResult(evidenceRecommendation, { tax_exclusions: taxExclusions });

    expect(screen.getByRole("heading", { name: "税务证据待补充" })).toBeTruthy();
    expect(screen.getByText(/未提供 HS Code、原产地、进口模式/)).toBeTruthy();
    const technical = screen.getByText("missing_customs_evidence");
    expect(technical.closest("details")?.open).toBe(false);
  });

  it("distinguishes an all-tax-evidence exclusion from a hard-constraint no-match", () => {
    renderCompletedResult(null, {
      product_evidence: [evidenceRecommendation],
      match_status: "no_match",
      tax_exclusions: [
        {
          item_id: "unclassified",
          platform: "ebay",
          title: "待归类商品",
          reason_code: "missing_customs_evidence",
          reason: "数据通道未提供完整税务证据。",
        },
      ],
    });

    expect(screen.getByText("税务证据不足，暂不能计算到手价")).toBeTruthy();
    expect(screen.queryByText("没有满足全部硬性条件的候选")).toBeNull();
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

    expect(screen.getByText("部分平台结果")).toBeTruthy();
    expect(screen.getByText(/eBay.*不可用/)).toBeTruthy();
    expect(screen.getByText("平台数据提供商通道请求失败")).toBeTruthy();
    expect(screen.getByText("平台请求失败（TimeoutException）")).toBeTruthy();
  });

  it("presents recall fallback as customer guidance and keeps the reason code collapsed", () => {
    renderCompletedResult(evidenceRecommendation, {
      recall_provenance: {
        mode: "deterministic_fallback",
        channels: {
          opensearch: unavailableRecallChannel("opensearch"),
          query_tower: unavailableRecallChannel("query_tower"),
          item_tower: unavailableRecallChannel("item_tower"),
          faiss: unavailableRecallChannel("faiss"),
        },
        participating_channels: [],
        fallback_reason: "optional_recall_unavailable",
        input_candidate_count: 12,
        selected_candidate_count: 12,
      },
    });

    const note = screen.getByRole("note", { name: "召回调整说明" });
    expect(note.textContent).toContain("召回已自动调整");
    expect(note.textContent).toContain("基础召回");
    expect(note.textContent).not.toContain("降级原因");

    const details = screen.getByText("技术详情").closest("details");
    expect(details?.open).toBe(false);
    expect(details?.textContent).toContain("optional_recall_unavailable");
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

  it("labels exact comparison mode, matching offers, alternatives, and identity evidence", () => {
    renderCompletedResult(matchingOffer, {
      mode: "exact_offer_comparison",
      matching_offers: [matchingOffer],
      comparison: [matchingOffer],
      alternative_candidates: [alternativeCandidate],
    });

    expect(screen.getByText("同款商品比价")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "已确认同款报价" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "相似商品候选" })).toBeTruthy();
    expect(screen.getAllByText("GTIN 已验证同款").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("只有标题相似，缺少可验证的跨平台身份或完整规格证据。")).toBeTruthy();
  });

  it("explains an exact-mode identity no-match separately from hard constraints", () => {
    renderCompletedResult(null, {
      mode: "exact_offer_comparison",
      matching_offers: [],
      comparison: [],
      alternative_candidates: [alternativeCandidate],
      match_status: "no_match",
    });

    expect(screen.getByText("没有证据充分的同款报价")).toBeTruthy();
    expect(screen.getByText("研究已完成，但没有证据充分的同款报价。")).toBeTruthy();
    expect(screen.queryByText("没有满足全部硬性条件的候选")).toBeNull();
  });
});
