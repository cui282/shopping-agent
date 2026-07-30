export const QUERY_MIN_LENGTH = 1;
export const QUERY_MAX_LENGTH = 4000;

export interface PreparedShoppingQuery {
  query: string | null;
  error: string | null;
}

export function queryCharacterCount(value: string): number {
  return Array.from(value).length;
}

export function prepareShoppingQuery(value: string): PreparedShoppingQuery {
  const query = value.trim();
  const length = queryCharacterCount(query);
  if (length < QUERY_MIN_LENGTH) return { query: null, error: "请输入商品研究需求" };
  if (length > QUERY_MAX_LENGTH) return { query: null, error: "购物需求不能超过 4000 个字符" };
  return { query, error: null };
}
