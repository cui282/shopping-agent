"""Deterministic Silent Drift signals and correction reminders."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def detect_drift(context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return advisory drift data without another model call or hidden state mutation."""

    query = str(context.get("query") or "").strip()
    tool_history = tuple(str(item) for item in context.get("tool_history", ()))
    result_text = str(context.get("tool_result", ""))
    preferences = context.get("preferences")
    preference_values = (
        [str(value) for values in preferences.values() for value in values]
        if isinstance(preferences, Mapping)
        else []
    )
    signals: list[str] = []
    if query and len(tool_history) >= 8 and "shopping_summary" not in tool_history:
        signals.append("cost_uncontrolled")
    if query and result_text and not any(token in result_text for token in query[:8]):
        signals.append("goal_fading")
    if (
        preference_values
        and result_text
        and not any(value in result_text for value in preference_values)
    ):
        signals.append("preference_lost")
    if len(tool_history) >= 4 and len(set(tool_history[-4:])) == 1:
        signals.append("exploration_loop")
    if not signals:
        return {"drift": {"detected": False, "signals": []}}
    severe = len(signals) >= 2
    reminder = (
        "[强制收尾] 检测到连续或多类方向漂移，请基于已有 Product Evidence 调用 shopping_summary。"
        if severe
        else "[方向提醒] 回到当前品类、预算和硬约束，避免继续扩展无关搜索。"
    )
    return {
        "drift": {"detected": True, "signals": signals, "severity": "severe" if severe else "mild"},
        "drift_reminder": reminder,
    }


__all__ = ["detect_drift"]
