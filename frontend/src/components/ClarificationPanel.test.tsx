// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ClarificationPanel from "./ClarificationPanel";

afterEach(cleanup);

const prompt = {
  field: "mode" as const,
  reason_code: "mode_ambiguous" as const,
  question: "你要比较不同产品，还是同一 Product Variant 的跨平台报价？",
};

describe("ClarificationPanel", () => {
  it("focuses the answer and submits one keyboard response", async () => {
    const onSubmit = vi.fn().mockResolvedValue({ ok: true });
    const restoreFocus = vi.fn();
    render(
      <ClarificationPanel prompt={prompt} onSubmit={onSubmit} onCancel={vi.fn()} onRestoreFocus={restoreFocus} />,
    );

    const input = screen.getByRole("textbox", { name: "澄清回答" });
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "比较不同产品" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("比较不同产品"));
    expect(restoreFocus).toHaveBeenCalledTimes(1);
  });

  it("announces a rejected response and keeps focus available", async () => {
    const onSubmit = vi.fn().mockResolvedValue({ ok: false, message: "请提供具体信息" });
    render(
      <ClarificationPanel prompt={prompt} onSubmit={onSubmit} onCancel={vi.fn()} onRestoreFocus={vi.fn()} />,
    );

    const input = screen.getByRole("textbox", { name: "澄清回答" });
    fireEvent.change(input, { target: { value: "都可以" } });
    fireEvent.click(screen.getByRole("button", { name: "提交回答" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("请提供具体信息"));
    expect(document.activeElement).toBe(input);
  });

  it("cancels with Escape", () => {
    const onCancel = vi.fn();
    render(
      <ClarificationPanel prompt={prompt} onSubmit={vi.fn()} onCancel={onCancel} onRestoreFocus={vi.fn()} />,
    );

    fireEvent.keyDown(screen.getByRole("textbox", { name: "澄清回答" }), { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
