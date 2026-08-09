"""Optional LangFuse tracing with a dependency-free no-op boundary.

The application remains usable without LangFuse. When the optional SDK and credentials are
available, tool spans and token route metadata are emitted; secrets and full prompts are never
included in event payloads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.agent.budget import TokenBudgetState
from app.config import get_settings


@dataclass(frozen=True, slots=True)
class ToolLatencyAlert:
    """A structured alert candidate that can be forwarded to a metrics backend."""

    tool_name: str
    duration_ms: int
    threshold_ms: int
    reason: str = "tool_latency_exceeded"


def tool_latency_alert(tool_name: str, duration_ms: int) -> ToolLatencyAlert | None:
    """Return an alert candidate without making logging or LangFuse a hard dependency."""

    try:
        threshold = max(1, int(os.getenv("OBS_TOOL_RT_ALERT_MS", "5000")))
    except ValueError:
        threshold = 5000
    if duration_ms <= threshold:
        return None
    return ToolLatencyAlert(
        tool_name=tool_name,
        duration_ms=max(0, duration_ms),
        threshold_ms=threshold,
    )


class TraceObserver:
    def start_trace(self, thread_id: str, *, query_length: int, data_mode: str) -> None:
        return None

    def end_trace(self, thread_id: str, *, status: str, budget: TokenBudgetState) -> None:
        return None

    def start_child_trace(
        self,
        parent_thread_id: str,
        child_thread_id: str,
        *,
        platform: str | None,
        demand_keys: list[str],
        fork_depth: int,
    ) -> None:
        return None

    def end_child_trace(self, child_thread_id: str, *, status: str) -> None:
        return None

    def tool_span(
        self,
        thread_id: str,
        *,
        name: str,
        duration_ms: int,
        status: str,
        route: str,
    ) -> None:
        return None

    def score(
        self,
        thread_id: str,
        *,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> None:
        return None


class LangFuseObserver(TraceObserver):
    def __init__(self) -> None:
        settings = get_settings()
        self._client: Any = None
        self._traces: dict[str, Any] = {}
        self._child_spans: dict[str, Any] = {}
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            return
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {
                "public_key": settings.langfuse_public_key,
                "secret_key": settings.langfuse_secret_key,
            }
            if settings.langfuse_base_url:
                kwargs["host"] = settings.langfuse_base_url
            self._client = Langfuse(**kwargs)
        except Exception:  # noqa: BLE001 - tracing must never block research
            # Optional observability must never make a research task unavailable.
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def start_trace(self, thread_id: str, *, query_length: int, data_mode: str) -> None:
        if self._client is None:
            return
        try:
            trace = self._client.trace(
                id=thread_id,
                name="shopping-agent-research",
                metadata={
                    "thread_id": thread_id,
                    "query_length": query_length,
                    "data_mode": data_mode,
                },
            )
            self._traces[thread_id] = trace
        except Exception:  # noqa: BLE001 - tracing must never block research
            return

    def start_child_trace(
        self,
        parent_thread_id: str,
        child_thread_id: str,
        *,
        platform: str | None,
        demand_keys: list[str],
        fork_depth: int,
    ) -> None:
        parent = self._traces.get(parent_thread_id)
        if parent is None:
            return
        try:
            self._child_spans[child_thread_id] = parent.span(
                name="shopping-agent-fork",
                metadata={
                    "thread_id": child_thread_id,
                    "parent_thread_id": parent_thread_id,
                    "platform": platform,
                    "demand_keys": demand_keys,
                    "fork_depth": fork_depth,
                },
            )
        except Exception:  # noqa: BLE001 - tracing must never block research
            return

    def end_child_trace(self, child_thread_id: str, *, status: str) -> None:
        span = self._child_spans.pop(child_thread_id, None)
        if span is None:
            return
        try:
            span.update(output={"status": status})
            span.end()
        except Exception:  # noqa: BLE001 - tracing must never block research
            return

    def end_trace(self, thread_id: str, *, status: str, budget: TokenBudgetState) -> None:
        trace = self._traces.pop(thread_id, None)
        if trace is None:
            return
        try:
            trace.update(
                output={
                    "status": status,
                    "token_budget": budget.budget,
                    "token_used": budget.used,
                    "route": budget.route,
                }
            )
            self._client.flush()
        except Exception:  # noqa: BLE001 - tracing must never block research
            return

    def tool_span(
        self,
        thread_id: str,
        *,
        name: str,
        duration_ms: int,
        status: str,
        route: str,
    ) -> None:
        trace = self._child_spans.get(thread_id) or self._traces.get(thread_id)
        if trace is None:
            return
        try:
            span = trace.span(
                name=name,
                metadata={
                    "thread_id": thread_id,
                    "duration_ms": duration_ms,
                    "status": status,
                    "route": route,
                },
            )
            span.end()
            alert = tool_latency_alert(name, duration_ms)
            if alert is not None:
                trace.update(
                    output={
                        "alert": alert.reason,
                        "tool_name": alert.tool_name,
                        "duration_ms": alert.duration_ms,
                        "threshold_ms": alert.threshold_ms,
                    }
                )
        except Exception:  # noqa: BLE001 - tracing must never block research
            return

    def score(
        self,
        thread_id: str,
        *,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> None:
        trace = self._traces.get(thread_id)
        if trace is None:
            return
        try:
            kwargs: dict[str, Any] = {"name": name, "value": max(0.0, min(1.0, value))}
            if comment:
                kwargs["comment"] = comment[:500]
            score_method = getattr(trace, "score", None)
            if callable(score_method):
                score_method(**kwargs)
            elif self._client is not None:
                self._client.score(trace_id=thread_id, **kwargs)
        except Exception:  # noqa: BLE001 - tracing must never block research
            return


_observer: TraceObserver | None = None


def get_observer() -> TraceObserver:
    global _observer
    if _observer is None:
        _observer = LangFuseObserver()
    return _observer


def reset_observer() -> None:
    global _observer
    _observer = None


__all__ = [
    "LangFuseObserver",
    "ToolLatencyAlert",
    "TraceObserver",
    "get_observer",
    "reset_observer",
    "tool_latency_alert",
]
