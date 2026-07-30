import { describe, expect, it } from "vitest";
import { prepareShoppingQuery, queryCharacterCount, QUERY_MAX_LENGTH } from "./queryContract";

describe("shopping query submission contract", () => {
  it.each(["a", "商".repeat(QUERY_MAX_LENGTH), ` \t${"商".repeat(QUERY_MAX_LENGTH)}\n`])("accepts a legal query", (query) => {
    expect(prepareShoppingQuery(query)).toEqual({ query: query.trim(), error: null });
  });

  it.each(["", " \t\n"])("rejects an empty or whitespace-only query", (query) => {
    expect(prepareShoppingQuery(query)).toEqual({ query: null, error: "请输入商品研究需求" });
  });

  it("rejects a query over 4000 characters", () => {
    expect(prepareShoppingQuery("商".repeat(QUERY_MAX_LENGTH + 1))).toEqual({
      query: null,
      error: "购物需求不能超过 4000 个字符",
    });
  });

  it("counts Unicode code points consistently with Pydantic", () => {
    const query = "🔭".repeat(QUERY_MAX_LENGTH);

    expect(queryCharacterCount(query)).toBe(QUERY_MAX_LENGTH);
    expect(prepareShoppingQuery(query)).toEqual({ query, error: null });
    expect(prepareShoppingQuery(`${query}🔭`).error).toBe("购物需求不能超过 4000 个字符");
  });
});
