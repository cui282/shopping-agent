"""Ordered lifecycle hooks for Agent Harness controls.

Hooks are deliberately independent from LangGraph callbacks. Callbacks observe model execution;
this pipeline may validate, modify, or reject an application-owned tool boundary.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

HookPoint = Literal[
    "on_session_start",
    "pre_think",
    "pre_tool_call",
    "post_tool_call",
    "post_reflect",
    "on_session_end",
]
HOOK_POINTS: frozenset[str] = frozenset(
    {
        "on_session_start",
        "pre_think",
        "pre_tool_call",
        "post_tool_call",
        "post_reflect",
        "on_session_end",
    }
)
HookContext = dict[str, Any]
HookFn = Callable[[HookContext], Awaitable[HookContext | None]]


class HookRejectSignal(RuntimeError):
    """A hook may reject an unsafe or phase-invalid operation explicitly."""


class HarnessMiddleware:
    """Run registered hooks in deterministic priority order.

    Ordinary hook failures are recorded in ``hook_errors`` and do not take down a research task;
    an explicit ``HookRejectSignal`` is a policy decision and is propagated to the caller.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[int, int, str, HookFn]]] = defaultdict(list)
        self._sequence = 0

    def register(self, hook_point: str, name: str, fn: HookFn, *, priority: int = 100) -> None:
        if hook_point not in HOOK_POINTS:
            raise ValueError(f"unknown hook point: {hook_point}")
        if not name.strip():
            raise ValueError("hook name must not be empty")
        self._sequence += 1
        self._hooks[hook_point].append((priority, self._sequence, name, fn))
        self._hooks[hook_point].sort(key=lambda item: (item[0], item[1]))

    def clear(self, hook_point: str | None = None) -> None:
        if hook_point is None:
            self._hooks.clear()
            return
        if hook_point not in HOOK_POINTS:
            raise ValueError(f"unknown hook point: {hook_point}")
        self._hooks.pop(hook_point, None)

    def registered(self, hook_point: str) -> tuple[str, ...]:
        return tuple(item[2] for item in self._hooks.get(hook_point, ()))

    async def run(
        self, hook_point: HookPoint, context: Mapping[str, Any] | None = None
    ) -> HookContext:
        current: HookContext = dict(context or {})
        current.setdefault("hook_errors", [])
        current["hook_point"] = hook_point
        for _, _, name, hook in self._hooks.get(hook_point, ()):
            try:
                result = await hook(current)
            except HookRejectSignal:
                raise
            except Exception as exc:  # noqa: BLE001 - optional controls fail open
                current["hook_errors"].append(
                    {"name": name, "error": type(exc).__name__, "message": str(exc)[:240]}
                )
                continue
            if result:
                current.update(result)
        return current


def harness_hook(hook_point: HookPoint, *, name: str, priority: int = 100):
    """Register a hook through a decorator on the process-wide Harness instance."""

    def decorator(fn: HookFn) -> HookFn:
        harness.register(hook_point, name, fn, priority=priority)
        return fn

    return decorator


harness = HarnessMiddleware()


__all__ = [
    "HOOK_POINTS",
    "HarnessMiddleware",
    "HookContext",
    "HookFn",
    "HookPoint",
    "HookRejectSignal",
    "harness",
    "harness_hook",
]
