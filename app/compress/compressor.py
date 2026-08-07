from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from app.compress.breakpoint import (
    CacheBreakpoint,
    ClarificationContext,
    ContextCompressionSettings,
    ContextMessage,
)
from app.schemas import (
    ClarificationPrompt,
    ContextClarificationResponse,
    ContextPreferenceSource,
    ContextSummary,
    MonitorEvent,
    RememberedPreference,
    ShoppingPlan,
    TaskOverride,
)

_TOKEN_CHARS = 4
_SUMMARY_ROLE = "system"
_SUMMARY_LABEL = "Shopping Agent structured task context"
_SUMMARY_FIELDS = (
    "resolved_hard_constraints",
    "product_variant",
    "exact_identity",
    "clarification_responses",
    "supported_destination",
    "working_assumptions",
    "remembered_preference",
    "task_overrides",
    "preference_sources",
)


class ModelContext:
    """A bounded, transient context window owned by the model boundary."""

    __slots__ = (
        "compressed_count",
        "estimated_tokens",
        "reason_code",
        "recent_messages",
        "status",
        "summary",
        "summary_text",
        "total_message_count",
    )

    def __init__(
        self,
        *,
        summary: ContextSummary,
        recent_messages: Sequence[ContextMessage],
        compressed_count: int,
        total_message_count: int,
        estimated_tokens: int,
        status: str,
        reason_code: str,
        summary_text: str,
    ) -> None:
        self.summary = summary
        self.recent_messages = tuple(recent_messages)
        self.compressed_count = compressed_count
        self.total_message_count = total_message_count
        self.estimated_tokens = estimated_tokens
        self.status = status
        self.reason_code = reason_code
        self.summary_text = summary_text

    @property
    def retained_message_count(self) -> int:
        return len(self.recent_messages)

    def to_model_messages(self) -> list[ContextMessage]:
        if not self.summary_text:
            return list(self.recent_messages)
        return [
            ContextMessage(role=_SUMMARY_ROLE, content=self.summary_text),
            *self.recent_messages,
        ]

    def compression_event_data(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "compressed_message_count": self.compressed_count,
            "retained_message_count": self.retained_message_count,
            "estimated_tokens": self.estimated_tokens,
            "summary_fields": list(_SUMMARY_FIELDS),
        }


def estimate_text_tokens(value: str) -> int:
    """Estimate tokens with a stable character metric independent of model vendors."""

    if not value:
        return 0
    return max(1, math.ceil(len(value) / _TOKEN_CHARS))


def estimate_context_tokens(messages: Iterable[ContextMessage | dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if isinstance(message, ContextMessage):
            role, content = message.role, message.content
        else:
            role = str(message.get("role", "user"))
            raw_content = message.get("content", "")
            content = raw_content if isinstance(raw_content, str) else _stable_json(raw_content)
        total += estimate_text_tokens(role) + estimate_text_tokens(content) + 1
    return total


def build_context_summary(
    *,
    plan: ShoppingPlan | None = None,
    product_variant: str | None = None,
    exact_identity: str | None = None,
    clarification_responses: Sequence[ClarificationContext | ContextClarificationResponse] = (),
    remembered_preference: RememberedPreference | None = None,
    task_overrides: Sequence[TaskOverride] = (),
    preference_sources: Sequence[ContextPreferenceSource] = (),
    pending_clarification: ClarificationPrompt | None = None,
) -> ContextSummary:
    """Rebuild model context facts from typed task state, never from a prior summary."""

    responses_by_field: dict[str, ContextClarificationResponse] = {}
    for response in clarification_responses:
        typed = (
            response
            if isinstance(response, ContextClarificationResponse)
            else ContextClarificationResponse(
                field=response.field,
                reason_code=response.reason_code,
                response=response.response,
                resolved_value=response.resolved_value,
            )
        )
        responses_by_field[typed.field] = typed
    responses = list(responses_by_field.values())

    effective_preferences = remembered_preference or RememberedPreference()
    overrides = list(task_overrides)
    sources = list(preference_sources)
    if not sources:
        sources = _preference_sources(effective_preferences, overrides)

    inferred_variant = next(
        (
            response.resolved_value or response.response
            for response in reversed(responses)
            if response.field == "product_variant"
        ),
        None,
    )
    variant = product_variant or inferred_variant
    mode = plan.mode if plan is not None else None

    return ContextSummary(
        mode=mode,
        category=plan.category if plan is not None else None,
        destination=plan.destination if plan is not None else None,
        supported_destination=(
            plan.destination if plan is not None and plan.destination == "中国大陆" else None
        ),
        resolved_hard_constraints=(list(plan.hard_constraints) if plan is not None else []),
        product_variant=variant,
        exact_identity=exact_identity or (variant if mode == "exact_offer_comparison" else None),
        clarification_responses=responses,
        working_assumptions=(list(plan.working_assumptions) if plan is not None else []),
        remembered_preference=effective_preferences,
        task_overrides=overrides,
        preference_sources=sources,
        pending_clarification=pending_clarification,
    )


def compress_model_context(
    messages: Sequence[ContextMessage | dict[str, Any]] | ModelContext,
    summary: ContextSummary | dict[str, Any] | None,
    settings: ContextCompressionSettings | None = None,
) -> ModelContext:
    """Return a fresh bounded context without changing any task-owned state."""

    config = settings or ContextCompressionSettings()
    inherited_count = 0
    inherited_status = "not_needed"
    inherited_summary_text = ""
    reuse_inherited_summary = False
    if isinstance(messages, ModelContext):
        source_messages = list(messages.recent_messages)
        inherited_count = messages.compressed_count
        inherited_status = messages.status
        inherited_summary_text = messages.summary_text
        total_message_count = max(messages.total_message_count, len(source_messages))
        if summary is None:
            summary = messages.summary
            reuse_inherited_summary = True
        else:
            reuse_inherited_summary = summary == messages.summary
    else:
        source_messages = _coerce_messages(messages)
        total_message_count = len(source_messages)

    typed_summary = _coerce_summary(summary)
    if typed_summary is None:
        return safe_bounded_context(
            source_messages,
            summary=None,
            settings=config,
            reason_code="invalid_summary",
        )
    summary = typed_summary

    full_messages = _coerce_messages(source_messages)
    full_tokens = estimate_context_tokens(full_messages)
    compressed = len(full_messages) > config.keep_recent or full_tokens > config.max_tokens
    if not compressed and inherited_status == "applied":
        compressed = True

    rendered_summary = (
        inherited_summary_text if reuse_inherited_summary else _render_summary(summary)
    )
    if (
        not compressed
        and estimate_context_tokens(
            [ContextMessage(role=_SUMMARY_ROLE, content=rendered_summary), *full_messages]
        )
        <= config.max_tokens
    ):
        return ModelContext(
            summary=summary,
            recent_messages=full_messages,
            compressed_count=inherited_count,
            total_message_count=total_message_count,
            estimated_tokens=estimate_context_tokens(
                [ContextMessage(role=_SUMMARY_ROLE, content=rendered_summary), *full_messages]
            ),
            status="not_needed",
            reason_code="below_threshold",
            summary_text=rendered_summary,
        )

    if not compressed:
        compressed = True
    compressed_count = max(inherited_count, max(0, total_message_count - config.keep_recent))
    recent = _fit_recent_messages(full_messages[-config.keep_recent :], rendered_summary, config)
    summary_text, recent, estimated_tokens = _fit_model_window(recent, rendered_summary, config)
    return ModelContext(
        summary=summary,
        recent_messages=recent,
        compressed_count=compressed_count,
        total_message_count=total_message_count,
        estimated_tokens=estimated_tokens,
        status="applied",
        reason_code="threshold_exceeded",
        summary_text=summary_text,
    )


def build_model_context(
    *,
    query: str,
    plan: ShoppingPlan | None,
    history_events: Sequence[MonitorEvent] = (),
    clarification_answers: dict[str, str] | None = None,
    remembered_preference: RememberedPreference | None = None,
    task_overrides: Sequence[TaskOverride] = (),
    preference_sources: Sequence[ContextPreferenceSource] = (),
    product_variant: str | None = None,
    pending_clarification: ClarificationPrompt | None = None,
    settings: ContextCompressionSettings | None = None,
) -> ModelContext:
    responses = _clarifications_from_events(history_events, clarification_answers or {})
    messages = build_context_messages(
        query=query,
        history_events=history_events,
        clarification_answers=clarification_answers,
    )
    summary = build_context_summary(
        plan=plan,
        product_variant=product_variant,
        clarification_responses=responses,
        remembered_preference=remembered_preference,
        task_overrides=task_overrides,
        preference_sources=preference_sources,
        pending_clarification=pending_clarification,
    )
    return compress_model_context(messages, summary, settings)


def build_context_summary_from_events(
    *,
    plan: ShoppingPlan | None = None,
    history_events: Sequence[MonitorEvent] = (),
    clarification_answers: dict[str, str] | None = None,
    product_variant: str | None = None,
    exact_identity: str | None = None,
    remembered_preference: RememberedPreference | None = None,
    task_overrides: Sequence[TaskOverride] = (),
    preference_sources: Sequence[ContextPreferenceSource] = (),
    pending_clarification: ClarificationPrompt | None = None,
) -> ContextSummary:
    """Build the typed summary projection directly from durable event inputs."""

    return build_context_summary(
        plan=plan,
        product_variant=product_variant,
        exact_identity=exact_identity,
        clarification_responses=_clarifications_from_events(
            history_events, clarification_answers or {}
        ),
        remembered_preference=remembered_preference,
        task_overrides=task_overrides,
        preference_sources=preference_sources,
        pending_clarification=pending_clarification,
    )


def build_context_messages(
    *,
    query: str,
    history_events: Sequence[MonitorEvent] = (),
    clarification_answers: dict[str, str] | None = None,
) -> list[ContextMessage]:
    """Rebuild model-visible conversation turns from durable typed events."""

    return _messages_from_events(query, history_events, clarification_answers or {})


def safe_bounded_context(
    messages: Sequence[ContextMessage | dict[str, Any]] | ModelContext,
    summary: ContextSummary | dict[str, Any] | None,
    settings: ContextCompressionSettings | None = None,
    *,
    reason_code: str = "compression_failed",
) -> ModelContext:
    """Stable last-resort path: preserve typed facts when possible and keep recent messages."""

    config = settings or ContextCompressionSettings()
    if isinstance(messages, ModelContext):
        source = list(messages.recent_messages)
        inherited_count = messages.compressed_count
        total_count = messages.total_message_count
        if summary is None:
            summary = messages.summary
    else:
        source = _coerce_messages(messages)
        inherited_count = 0
        total_count = len(source)
    safe_summary = _coerce_summary(summary) or ContextSummary()
    rendered = _render_summary(safe_summary)
    recent = _fit_recent_messages(source[-config.keep_recent :], rendered, config)
    summary_text, recent, estimated_tokens = _fit_model_window(recent, rendered, config)
    return ModelContext(
        summary=safe_summary,
        recent_messages=recent,
        compressed_count=max(inherited_count, max(0, total_count - len(recent))),
        total_message_count=total_count,
        estimated_tokens=estimated_tokens,
        status="degraded",
        reason_code=reason_code,
        summary_text=summary_text,
    )


def compress_messages(
    messages: list[dict[str, Any]],
    *,
    settings: ContextCompressionSettings | None = None,
) -> CacheBreakpoint:
    """Compatibility wrapper for the original untyped cache-breakpoint helper."""

    config = settings or _settings_from_env()
    source = _coerce_messages(messages)
    full_tokens = estimate_context_tokens(source)
    should_compress = len(source) > config.keep_recent or full_tokens > config.max_tokens
    if not should_compress:
        return CacheBreakpoint(
            recent=list(messages),
            estimated_tokens=full_tokens,
            status="not_needed",
            reason_code="below_threshold",
        )
    recent = list(messages[-config.keep_recent :])
    older = messages[: -config.keep_recent]
    summary = "\n".join(
        f"{item.get('role', 'event')}: {_stable_json(item.get('content', item))[:240]}"
        for item in older
    )
    window = safe_bounded_context(
        source,
        ContextSummary(),
        config,
        reason_code="compression_failed" if not summary else "threshold_exceeded",
    )
    return CacheBreakpoint(
        summary=summary,
        recent=recent,
        compressed_count=len(older),
        estimated_tokens=window.estimated_tokens,
        status=window.status if not summary else "applied",
        reason_code=window.reason_code if not summary else "threshold_exceeded",
    )


def _settings_from_env() -> ContextCompressionSettings:
    def integer(name: str, default: int) -> int:
        raw = os.getenv(name, str(default)).strip()
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    return ContextCompressionSettings(
        keep_recent=integer("COMPRESS_KEEP_RECENT", 3),
        max_tokens=max(32, integer("COMPRESS_MAX_TOKENS", 12_000)),
    )


def _preference_sources(
    remembered: RememberedPreference,
    overrides: Sequence[TaskOverride],
) -> list[ContextPreferenceSource]:
    sources: list[ContextPreferenceSource] = []
    for field in RememberedPreference.model_fields:
        for value in getattr(remembered, field):
            sources.append(
                ContextPreferenceSource(field=field, value=value, source="remembered_preference")
            )
    sources.extend(
        ContextPreferenceSource(field=item.field, value=item.value, source="task_override")
        for item in overrides
    )
    return sources


def _clarifications_from_events(
    events: Sequence[MonitorEvent], answers: dict[str, str]
) -> list[ContextClarificationResponse]:
    responses: list[ContextClarificationResponse] = []
    for event in events:
        if event.event != "clarification_resolved":
            continue
        try:
            responses.append(ContextClarificationResponse.model_validate(event.data))
        except ValueError:
            continue
    known_fields = {item.field for item in responses}
    reason_codes = {
        "mode": "mode_ambiguous",
        "product_variant": "product_variant_ambiguous",
        "destination": "destination_ambiguous",
    }
    for field, value in answers.items():
        if field in known_fields or field not in reason_codes or not value.strip():
            continue
        responses.append(
            ContextClarificationResponse(
                field=field,
                reason_code=reason_codes[field],
                response=value,
                resolved_value=value,
            )
        )
    return responses


def _messages_from_events(
    query: str,
    events: Sequence[MonitorEvent],
    answers: dict[str, str],
) -> list[ContextMessage]:
    messages = [ContextMessage(role="user", content=query)]
    recorded_responses: set[str] = set()
    for event in events:
        if event.event == "clarification_required":
            question = event.data.get("question")
            if isinstance(question, str) and question.strip():
                messages.append(ContextMessage(role="assistant", content=question.strip()))
        elif event.event == "clarification_resolved":
            response = event.data.get("response")
            if isinstance(response, str) and response.strip():
                messages.append(ContextMessage(role="user", content=response.strip()))
                field = event.data.get("field")
                if isinstance(field, str):
                    recorded_responses.add(field)
    for field, response in answers.items():
        if field not in recorded_responses and isinstance(response, str) and response.strip():
            messages.append(ContextMessage(role="user", content=response.strip()))
    return messages


def _coerce_messages(messages: Sequence[ContextMessage | dict[str, Any]]) -> list[ContextMessage]:
    result: list[ContextMessage] = []
    for message in messages:
        if isinstance(message, ContextMessage):
            result.append(message)
            continue
        role = message.get("role", "user")
        content = message.get("content", "")
        if not isinstance(role, str) or role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        if not isinstance(content, str):
            content = _stable_json(content)
        if content.strip():
            result.append(ContextMessage(role=role, content=content))  # type: ignore[arg-type]
    return result


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _render_summary(summary: ContextSummary) -> str:
    def scalar(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    constraints = (
        ";".join(
            ":".join([item.field, item.operator, scalar(item.value), scalar(item.unit)])
            for item in summary.resolved_hard_constraints
        )
        or "[]"
    )
    clarifications = (
        ";".join(
            ":".join([item.field, scalar(item.resolved_value), scalar(item.response)])
            for item in summary.clarification_responses
        )
        or "[]"
    )
    assumptions = (
        ";".join(":".join([item.field, scalar(item.value)]) for item in summary.working_assumptions)
        or "[]"
    )
    preferences = (
        ";".join(
            f"{field}={scalar(value)}"
            for field in RememberedPreference.model_fields
            for value in getattr(summary.remembered_preference, field)
        )
        or "[]"
    )
    overrides = (
        ";".join(
            f"{item.field}={scalar(item.value)}>{scalar(item.overridden_values)}"
            for item in summary.task_overrides
        )
        or "[]"
    )
    sources = (
        ";".join(
            f"{item.field}={scalar(item.value)}@{item.source}"
            for item in summary.preference_sources
        )
        or "[]"
    )
    lines = [
        _SUMMARY_LABEL,
        f"resolved_hard_constraints={constraints}",
        f"product_variant={scalar(summary.product_variant)}",
        f"exact_identity={scalar(summary.exact_identity)}",
        f"clarification_responses={clarifications}",
        f"supported_destination={scalar(summary.supported_destination)}",
        f"working_assumptions={assumptions}",
        f"remembered_preference={preferences}",
        f"task_overrides={overrides}",
        f"preference_sources={sources}",
    ]
    if summary.pending_clarification is not None:
        lines.append(
            "pending_clarification=" + scalar(summary.pending_clarification.model_dump(mode="json"))
        )
    return "\n".join(lines)


def _fit_recent_messages(
    recent: Sequence[ContextMessage], summary_text: str, settings: ContextCompressionSettings
) -> list[ContextMessage]:
    del summary_text, settings
    return list(recent)


def _fit_model_window(
    recent: Sequence[ContextMessage], summary_text: str, settings: ContextCompressionSettings
) -> tuple[str, list[ContextMessage], int]:
    # Reserve one minimal recent message so the bounded window remains useful for continuation.
    reserved_recent_tokens = estimate_context_tokens([recent[-1]]) if recent else 0
    summary_content_tokens = max(
        1,
        settings.max_tokens
        - min(reserved_recent_tokens, settings.max_tokens - 1)
        - estimate_text_tokens(_SUMMARY_ROLE)
        - 1,
    )
    fitted_summary = _fit_text(summary_text, summary_content_tokens * _TOKEN_CHARS)
    summary_message = ContextMessage(role=_SUMMARY_ROLE, content=fitted_summary)
    remaining = max(0, settings.max_tokens - estimate_context_tokens([summary_message]))
    fitted_recent: list[ContextMessage] = []
    for message in reversed(recent):
        cost = estimate_context_tokens([message])
        if cost <= remaining:
            fitted_recent.append(message)
            remaining -= cost
            continue
        if not fitted_recent:
            content_tokens = remaining - estimate_text_tokens(message.role) - 1
            if content_tokens >= 1:
                fitted = replace(
                    message,
                    content=_fit_text(message.content, content_tokens * _TOKEN_CHARS),
                )
                if estimate_context_tokens([fitted]) <= remaining:
                    fitted_recent.append(fitted)
        break
    fitted_recent.reverse()

    model_messages = [summary_message, *fitted_recent]
    total = estimate_context_tokens(model_messages)
    while total > settings.max_tokens and fitted_recent:
        fitted_recent.pop(0)
        model_messages = [summary_message, *fitted_recent]
        total = estimate_context_tokens(model_messages)
    if total > settings.max_tokens:
        summary_tokens = max(
            1,
            settings.max_tokens - estimate_text_tokens(_SUMMARY_ROLE) - 1,
        )
        fitted_summary = _fit_text(summary_text, summary_tokens * _TOKEN_CHARS)
        model_messages = [ContextMessage(role=_SUMMARY_ROLE, content=fitted_summary)]
        total = estimate_context_tokens(model_messages)
    return fitted_summary, fitted_recent, total


def _coerce_summary(value: ContextSummary | dict[str, Any] | None) -> ContextSummary | None:
    if isinstance(value, ContextSummary):
        return value
    if value is None:
        return None
    try:
        return ContextSummary.model_validate(value)
    except (TypeError, ValueError):
        return None


def _fit_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 24:
        return value[:max_chars]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    marker = f"...[content:{digest}]..."
    side = max(1, (max_chars - len(marker)) // 2)
    return f"{value[:side]}{marker}{value[-side:]}"


__all__ = [
    "ModelContext",
    "build_context_messages",
    "build_context_summary",
    "build_context_summary_from_events",
    "build_model_context",
    "compress_messages",
    "compress_model_context",
    "estimate_context_tokens",
    "estimate_text_tokens",
    "safe_bounded_context",
]
