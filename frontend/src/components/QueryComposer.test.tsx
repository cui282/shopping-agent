// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import QueryComposer from "./QueryComposer";

afterEach(cleanup);

function renderComposer(value: string, onSubmit = vi.fn()) {
  render(
    <QueryComposer
      value={value}
      busy={false}
      canCancel={false}
      disabledReason={null}
      allowImageUpload={false}
      attachmentResetKey={0}
      onChange={vi.fn()}
      onSubmit={onSubmit}
      onCancel={vi.fn()}
    />,
  );
  return onSubmit;
}

describe("QueryComposer submission", () => {
  it("submits a one-character query with the command button", () => {
    const onSubmit = renderComposer("a");

    fireEvent.click(screen.getByRole("button", { name: "开始研究" }));

    expect(onSubmit).toHaveBeenCalledWith([]);
  });

  it("submits with Enter while preserving Shift+Enter for editing", () => {
    const onSubmit = renderComposer("找望远镜");
    const textbox = screen.getByRole("textbox", { name: "购物需求" });

    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("rejects whitespace-only submission, focuses the field, and announces the error", () => {
    const onSubmit = renderComposer(" \t\n ");

    fireEvent.click(screen.getByRole("button", { name: "开始研究" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "购物需求" }));
    expect(screen.getByRole("status").textContent).toBe("请输入商品研究需求");
  });
});
