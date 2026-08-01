// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initialAgentState } from "../hooks/useShoppingAgent";
import type { Recommendation } from "../types/api";
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
  reason: "到手价与证据完整",
  rank: 1,
};

function renderCompletedResult(recommendation = evidenceRecommendation) {
  render(
    <ResearchContent
      state={{
        ...initialAgentState,
        status: "completed",
        result: {
          thread_id: "thread-evidence",
          final_answer: "已按可核验证据整理结果。",
          recommendations: [recommendation],
          comparison: [],
          files: [],
          provider_mode: "live",
          providers: {},
          calculation_notice: "价格为抓取时点信息。",
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
});
