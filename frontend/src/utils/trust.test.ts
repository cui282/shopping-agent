import { describe, expect, it } from "vitest";
import type { ReadinessResponse } from "../types/api";
import { providerModeLabel, providerReasonLabel, taskDisabledReason } from "./trust";

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
};

describe("trust state", () => {
  it("blocks task creation until readiness is known", () => {
    expect(taskDisabledReason("checking", null)).toContain("检查");
    expect(taskDisabledReason("available", { ...ready, task_ready: false, status: "not_ready" })).toContain("尚未配置");
    expect(taskDisabledReason("available", ready)).toBeNull();
  });

  it("uses an explicit unverified source state", () => {
    expect(providerModeLabel("unverified")).toBe("来源待确认");
    expect(providerModeLabel("sandbox")).toBe("沙盒来源");
    expect(providerReasonLabel("SANDBOX_MODE is enabled")).toBe("已显式启用沙盒模式");
  });
});
