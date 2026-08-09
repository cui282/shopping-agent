"""Execution guards shared by model-driven and deterministic agent paths."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

from app.config import get_settings


class AgentLoopGuardError(RuntimeError):
    """A model loop exceeded one of the bounded execution policies."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ToolGuardState:
    total_calls: int = 0
    calls: tuple[tuple[str, str], ...] = ()


_state: ContextVar[ToolGuardState | None] = ContextVar("agent_tool_guard_state", default=None)


def reset_tool_guard() -> None:
    """Reset the per-task guard without leaking state between tasks."""

    _state.set(ToolGuardState())


def tool_call_state() -> ToolGuardState:
    return _state.get() or ToolGuardState()


def _stable_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def record_tool_call(name: str, args: dict[str, Any]) -> ToolGuardState:
    """Record a call and reject runaway or identical model loops."""

    settings = get_settings()
    state = _state.get() or ToolGuardState()
    key = (name, _stable_args(args))
    same_calls = sum(1 for previous in state.calls if previous == key)
    if state.total_calls >= settings.agent_max_tool_calls:
        raise AgentLoopGuardError(
            "tool_call_limit",
            f"agent tool-call limit ({settings.agent_max_tool_calls}) was reached",
        )
    if same_calls >= settings.agent_loop_detection_threshold:
        raise AgentLoopGuardError(
            "loop_detected",
            f"agent repeated {name} with identical arguments too many times",
        )
    next_state = replace(
        state,
        total_calls=state.total_calls + 1,
        calls=(*state.calls, key),
    )
    _state.set(next_state)
    return next_state


def bounded_tool_result(value: Any, *, max_chars: int | None = None) -> str:
    """Render a model-visible tool result within the configured character budget."""

    limit = max_chars or get_settings().agent_tool_result_chars
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    # Keep the model boundary safe when a future tool exposes external text directly.
    from app.security import sanitize_tool_output

    rendered = sanitize_tool_output(rendered)
    if len(rendered) <= limit:
        return rendered
    suffix = "… [tool result truncated]"
    return rendered[: max(0, limit - len(suffix))] + suffix


__all__ = [
    "AgentLoopGuardError",
    "ToolGuardState",
    "bounded_tool_result",
    "record_tool_call",
    "reset_tool_guard",
    "tool_call_state",
]
