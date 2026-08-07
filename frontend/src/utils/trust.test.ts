import { describe, expect, it } from "vitest";
import type { ReadinessResponse } from "../types/api";
import { providerModeLabel, providerReasonLabel, requiredActionLabel, taskDisabledReason } from "./trust";

const ready: ReadinessResponse = {
  status: "ready",
  task_ready: true,
  environment: "production",
  runtime_mode: "live",
  agent_mode: "llm",
  requested_agent_mode: "auto",
  preference_store: "redis",
  providers: {},
  capabilities: { image_analysis: false },
  required_actions: [],
  data_mode: "live",
  developer_diagnostic_mode: false,
};

describe("trust state", () => {
  it("blocks task creation until readiness is known", () => {
    expect(taskDisabledReason("checking", null)).toContain("检查");
    expect(taskDisabledReason("available", { ...ready, task_ready: false, status: "not_ready" })).toContain("尚未配置");
    expect(taskDisabledReason("available", ready)).toBeNull();
  });

  it("uses an explicit unverified source state", () => {
    expect(providerModeLabel("unverified")).toBe("Result source pending");
    expect(providerModeLabel("sandbox")).toBe("Sandbox Result");
    expect(providerReasonLabel("SANDBOX_MODE is enabled")).toBe("已显式启用沙盒模式");
  });

  it("describes data-provider channel configuration without implying official marketplace keys", () => {
    expect(
      requiredActionLabel(
        "Configure at least one data-provider marketplace channel endpoint/credential pair, or explicitly enable SANDBOX_MODE for local testing",
      ),
    ).toBe("至少配置一个数据提供商平台通道，本地验证也可显式启用 SANDBOX_MODE");
  });
});
