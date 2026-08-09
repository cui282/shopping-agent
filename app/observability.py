"""Optional LangFuse tracing with a dependency-free no-op boundary.

The application remains usable without LangFuse. When the optional SDK and credentials are
available, tool spans and token route metadata are emitted; secrets and full prompts are never
included in event payloads.
"""

from __future__ import annotations

from typing import Any

from app.agent.budget import TokenBudgetState
from app.config import get_settings


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


__all__ = ["LangFuseObserver", "TraceObserver", "get_observer", "reset_observer"]
