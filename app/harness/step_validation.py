"""Low-cost process assertions used by the Harness post-tool hooks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_EXPECTED_PREDECESSORS: dict[str, frozenset[str]] = {
    "category_insight": frozenset({"planner", "task_tool", "category_insight"}),
    "item_search": frozenset({"planner", "task_tool", "category_insight", "item_search"}),
    "recall": frozenset({"item_search", "category_insight", "recall"}),
    "price_compare": frozenset({"recall", "item_search"}),
    "shipping_calc": frozenset({"price_compare", "shipping_calc"}),
    "item_picker": frozenset({"shipping_calc", "item_picker"}),
    "shopping_summary": frozenset({"item_picker", "shopping_summary"}),
}


def _fail(context: Mapping[str, Any], code: str, explanation: str) -> dict[str, Any]:
    failures = list(context.get("assertions_failed", ()))
    failures.append({"type": code, "explanation": explanation})
    return {"assertions_failed": failures}


async def check_schema(context: dict[str, Any]) -> dict[str, Any] | None:
    result = context.get("tool_result")
    if result is None or not callable(getattr(result, "model_dump", None)):
        return _fail(context, "schema", "工具返回不是受支持的 Pydantic 结果对象。")
    try:
        result.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - assertion is advisory
        return _fail(context, "schema", f"工具结果无法序列化：{type(exc).__name__}")
    return None


async def check_sequencing(context: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(context.get("tool_name", ""))
    history = tuple(str(item) for item in context.get("tool_history", ()))
    expected = _EXPECTED_PREDECESSORS.get(tool_name)
    result: dict[str, Any] = {"tool_history": (*history, tool_name)[-32:]}
    if expected and not any(item in expected for item in history[-4:]):
        result.update(_fail(context, "sequencing", f"{tool_name} 前缺少合法的前置步骤。"))
    return result


async def check_semantic_alignment(context: dict[str, Any]) -> dict[str, Any] | None:
    query = str(context.get("query") or context.get("tool_args", {}).get("query") or "")
    if not query:
        return None
    text = str(context.get("tool_result", ""))
    query_tokens = {token.casefold() for token in _TOKEN_RE.findall(query) if token.strip()}
    result_tokens = {token.casefold() for token in _TOKEN_RE.findall(text) if token.strip()}
    if query_tokens and result_tokens and not query_tokens.intersection(result_tokens):
        return _fail(context, "semantic", "工具结果与当前购物意图没有可验证的词面重合。")
    return None


__all__ = ["check_schema", "check_semantic_alignment", "check_sequencing"]
