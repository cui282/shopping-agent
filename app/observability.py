"""Langfuse tracing with a credential-optional no-op boundary.

The application remains usable without Langfuse credentials. When credentials are available,
observations cover the full workflow; secrets and full prompts are never included in metadata.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from time import monotonic
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
        observation_type: str = "tool",
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
    ) -> None:
        return None

    def tool_start(
        self,
        thread_id: str,
        *,
        name: str,
        route: str,
        observation_type: str = "tool",
        model: str | None = None,
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
        self._modern = False
        self._configured = bool(settings.langfuse_public_key and settings.langfuse_secret_key)
        self._initialization_reason = (
            "configured_not_probed" if self._configured else "not_configured"
        )
        self._tracing_enabled = os.getenv(
            "LANGFUSE_TRACING_ENABLED", "true"
        ).strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self._traces: dict[str, Any] = {}
        self._child_spans: dict[str, Any] = {}
        self._trace_ids: dict[str, str] = {}
        self._trace_started_at: dict[str, float] = {}
        self._child_started_at: dict[str, float] = {}
        self._tool_observations: dict[tuple[str, str], list[Any]] = {}
        if not self._configured:
            return
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {
                "public_key": settings.langfuse_public_key,
                "secret_key": settings.langfuse_secret_key,
            }
            if settings.langfuse_base_url:
                kwargs["host"] = settings.langfuse_base_url
            kwargs["tracing_enabled"] = self._tracing_enabled
            environment = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "").strip()
            if environment:
                kwargs["environment"] = environment
            release = os.getenv("RELEASE_ID", "").strip()
            if release:
                kwargs["release"] = release
            self._client = Langfuse(**kwargs)
            self._modern = callable(getattr(self._client, "start_observation", None))
            self._initialization_reason = (
                "configured_not_probed" if self._tracing_enabled else "disabled_by_config"
            )
        except ImportError:
            self._initialization_reason = "sdk_not_installed"
            self._client = None
        except Exception:  # noqa: BLE001 - tracing must never block research
            # Optional observability must never make a research task unavailable.
            self._initialization_reason = "initialization_failed"
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None and self._tracing_enabled

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def initialization_reason(self) -> str:
        return self._initialization_reason

    def _modern_enabled(self) -> bool:
        client = getattr(self, "_client", None)
        return bool(
            client is not None
            and (
                getattr(self, "_modern", False)
                or callable(getattr(client, "start_observation", None))
            )
            and getattr(self, "_tracing_enabled", True)
        )

    def _trace_context(self, thread_id: str) -> dict[str, str] | None:
        trace_ids = getattr(self, "_trace_ids", {})
        trace_id = trace_ids.get(thread_id)
        parent = getattr(self, "_child_spans", {}).get(thread_id) or getattr(
            self, "_traces", {}
        ).get(thread_id)
        parent_span_id = getattr(parent, "id", None)
        context: dict[str, str] = {}
        if isinstance(trace_id, str) and trace_id:
            context["trace_id"] = trace_id
        if isinstance(parent_span_id, str) and parent_span_id:
            context["parent_span_id"] = parent_span_id
        return context or None

    def _start_observation(
        self,
        thread_id: str,
        *,
        name: str,
        as_type: str,
        metadata: dict[str, Any],
        model: str | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "name": name,
            "as_type": as_type,
            "metadata": metadata,
        }
        context = self._trace_context(thread_id)
        if context:
            kwargs["trace_context"] = context
        if model:
            kwargs["model"] = model
        return self._client.start_observation(**kwargs)

    def start_trace(self, thread_id: str, *, query_length: int, data_mode: str) -> None:
        if self._client is None:
            return
        if self._modern_enabled():
            try:
                trace_id = self._client.create_trace_id(seed=thread_id)
                trace = self._client.start_observation(
                    trace_context={"trace_id": trace_id},
                    name="shopping-agent-research",
                    as_type="agent",
                    metadata={
                        "thread_id": thread_id,
                        "query_length": query_length,
                        "data_mode": data_mode,
                    },
                )
                self._traces[thread_id] = trace
                self._trace_ids[thread_id] = trace_id
                self._trace_started_at[thread_id] = monotonic()
            except Exception:  # noqa: BLE001 - tracing must never block research
                return
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
        if self._modern_enabled():
            try:
                trace_id = self._trace_ids.get(parent_thread_id)
                context = {"trace_id": trace_id} if trace_id else None
                parent_span_id = getattr(parent, "id", None)
                if parent_span_id:
                    context = {**(context or {}), "parent_span_id": parent_span_id}
                kwargs: dict[str, Any] = {
                    "name": "shopping-agent-fork",
                    "as_type": "chain",
                    "metadata": {
                        "thread_id": child_thread_id,
                        "parent_thread_id": parent_thread_id,
                        "platform": platform,
                        "demand_keys": demand_keys,
                        "fork_depth": fork_depth,
                    },
                }
                if context:
                    kwargs["trace_context"] = context
                self._child_spans[child_thread_id] = self._client.start_observation(**kwargs)
                if trace_id:
                    self._trace_ids[child_thread_id] = trace_id
                self._child_started_at[child_thread_id] = monotonic()
            except Exception:  # noqa: BLE001 - tracing must never block research
                return
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
        if self._modern_enabled():
            duration_ms = round(
                (monotonic() - self._child_started_at.pop(child_thread_id, monotonic())) * 1000
            )
            try:
                span.update(
                    output={"status": status, "duration_ms": max(0, duration_ms)},
                    metadata={"duration_ms": max(0, duration_ms)},
                    status_message=status,
                )
                span.end()
            except Exception:  # noqa: BLE001 - tracing must never block research
                return
            self._trace_ids.pop(child_thread_id, None)
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
        if self._modern_enabled():
            duration_ms = round(
                (monotonic() - self._trace_started_at.pop(thread_id, monotonic())) * 1000
            )
            self._trace_ids.pop(thread_id, None)
            try:
                trace.update(
                    output={
                        "status": status,
                        "duration_ms": max(0, duration_ms),
                        "token_budget": budget.budget,
                        "token_used": budget.used,
                        "route": budget.route,
                    },
                    metadata={"duration_ms": max(0, duration_ms)},
                )
                trace.end()
                self._client.flush()
            except Exception:  # noqa: BLE001 - tracing must never block research
                return
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
        observation_type: str = "tool",
        model: str | None = None,
        usage_details: dict[str, int] | None = None,
    ) -> None:
        if self._modern_enabled():
            key = (thread_id, name)
            observations = getattr(self, "_tool_observations", {}).get(key, [])
            span = observations.pop() if observations else None
            if not observations:
                getattr(self, "_tool_observations", {}).pop(key, None)
            if span is None:
                try:
                    span = self._start_observation(
                        thread_id,
                        name=name,
                        as_type=observation_type,
                        metadata={"thread_id": thread_id, "route": route},
                        model=model,
                    )
                except Exception:  # noqa: BLE001 - tracing must never block research
                    return
            metadata: dict[str, Any] = {
                "duration_ms": max(0, duration_ms),
                "status": status,
                "route": route,
            }
            alert = tool_latency_alert(name, duration_ms)
            if alert is not None:
                metadata.update(
                    {
                        "latency_alert": alert.reason,
                        "latency_threshold_ms": alert.threshold_ms,
                    }
                )
            if model:
                metadata["model"] = model
            if usage_details:
                metadata["usage_details"] = usage_details
            update_kwargs: dict[str, Any] = {
                "output": {"status": status, "duration_ms": max(0, duration_ms)},
                "metadata": metadata,
                "status_message": status,
            }
            if model:
                update_kwargs["model"] = model
            if usage_details:
                update_kwargs["usage_details"] = usage_details
            try:
                span.update(**update_kwargs)
                span.end()
            except Exception:  # noqa: BLE001 - tracing must never block research
                return
            return
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

    def tool_start(
        self,
        thread_id: str,
        *,
        name: str,
        route: str,
        observation_type: str = "tool",
        model: str | None = None,
    ) -> None:
        if not self._modern_enabled():
            return
        try:
            span = self._start_observation(
                thread_id,
                name=name,
                as_type=observation_type,
                metadata={"thread_id": thread_id, "route": route},
                model=model,
            )
            self._tool_observations.setdefault((thread_id, name), []).append(span)
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
