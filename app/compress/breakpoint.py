from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas import (
    ClarificationField,
    ClarificationReasonCode,
    ContextMessageRole,
    ContextPreferenceSource,
    ContextSummary,
)


@dataclass(frozen=True, slots=True)
class ContextCompressionSettings:
    """Provider-independent bounds for the model-only context window."""

    keep_recent: int = 3
    max_tokens: int = 12_000

    def __post_init__(self) -> None:
        if self.keep_recent < 1:
            raise ValueError("keep_recent must be at least 1")
        if self.max_tokens < 32:
            raise ValueError("max_tokens must be at least 32")


@dataclass(frozen=True, slots=True)
class ContextMessage:
    role: ContextMessageRole
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("context message content must not be empty")


@dataclass(frozen=True, slots=True)
class ClarificationContext:
    field: ClarificationField
    reason_code: ClarificationReasonCode
    response: str
    resolved_value: str | None = None


@dataclass(slots=True)
class CacheBreakpoint:
    """Backward-compatible projection for callers of the original compressor."""

    summary: str = ""
    recent: list[dict[str, Any]] = field(default_factory=list)
    compressed_count: int = 0
    estimated_tokens: int = 0
    status: str = "not_needed"
    reason_code: str = "below_threshold"
    typed_summary: ContextSummary | None = None


__all__ = [
    "CacheBreakpoint",
    "ClarificationContext",
    "ContextCompressionSettings",
    "ContextMessage",
    "ContextPreferenceSource",
]
