import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { SessionHistoryItem } from "../types/api";
import SessionRail from "./SessionRail";

describe("SessionRail", () => {
  it("offers a delete action for every recent research session", () => {
    const history: SessionHistoryItem[] = [
      {
        threadId: "thread-phone",
        query: "找一款适合长辈使用的手机",
        status: "completed",
        createdAt: "2026-07-30T08:00:00Z",
      },
    ];

    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <SessionRail
          history={history}
          activeThreadId={null}
          providerMode="sandbox"
          onNew={vi.fn()}
          onSelect={vi.fn()}
          onDelete={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(markup).toContain('aria-label="删除研究：找一款适合长辈使用的手机"');
  });

  it("prevents reopening a session while its deletion is pending", () => {
    const session: SessionHistoryItem = {
      threadId: "thread-phone",
      query: "找一款适合长辈使用的手机",
      status: "completed",
      createdAt: "2026-07-30T08:00:00Z",
    };

    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <SessionRail
          history={[session]}
          activeThreadId={null}
          providerMode="sandbox"
          onNew={vi.fn()}
          onSelect={vi.fn()}
          onDelete={vi.fn()}
          deletingThreadId={session.threadId}
        />
      </MemoryRouter>,
    );

    expect(markup.match(/disabled=""/g)).toHaveLength(2);
  });
});
