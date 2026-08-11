// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initialAgentState } from "../hooks/useShoppingAgent";
import type { ReadinessResponse } from "../types/api";
import ReadinessNotice from "./ReadinessNotice";

afterEach(cleanup);

const sandboxReadiness: ReadinessResponse = {
  status: "degraded",
  task_ready: true,
  environment: "development",
  runtime_mode: "sandbox",
  agent_mode: "rules",
  requested_agent_mode: "auto",
  preference_store: "memory",
  providers: {
    amazon: {
      configured: true,
      state: "configured",
      available: true,
      source: "fixture",
      failure_reason: null,
    },
  },
  capabilities: { image_analysis: false },
  required_actions: ["Configure a live provider channel"],
  data_mode: "sandbox",
  developer_diagnostic_mode: false,
};

describe("ReadinessNotice", () => {
  it("keeps non-blocking sandbox diagnostics collapsed behind a customer-facing disclosure", () => {
    const onRefresh = vi.fn();
    render(
      <ReadinessNotice
        state={{
          ...initialAgentState,
          serviceStatus: "available",
          readiness: sandboxReadiness,
        }}
        onRefresh={onRefresh}
      />,
    );

    const title = screen.getByText("当前使用演示数据", { exact: true });
    const disclosure = title.closest("details") as HTMLDetailsElement;
    expect(disclosure.open).toBe(false);
    expect(screen.getByRole("status", { name: "数据来源状态" }).textContent).toContain(
      "价格和库存可能不是实时信息",
    );

    fireEvent.click(title);
    expect(disclosure.open).toBe(true);
    expect(disclosure.textContent).toContain("购买前请以平台结算页为准");
    expect(disclosure.textContent).toContain("Amazon");

    fireEvent.click(screen.getByRole("button", { name: "重新检查数据来源" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("keeps blocking configuration failures expanded", () => {
    render(
      <ReadinessNotice
        state={{
          ...initialAgentState,
          serviceStatus: "available",
          readiness: {
            ...sandboxReadiness,
            status: "not_ready",
            task_ready: false,
          },
        }}
        onRefresh={vi.fn()}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("服务尚未完成运行配置");
    expect(alert.textContent).toContain("完成以下配置后才能启动购物研究");
    expect(screen.queryByText("当前使用演示数据", { exact: true })).toBeNull();
  });
});
