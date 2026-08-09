from __future__ import annotations

from collections.abc import Sequence
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


def _message_role(message: Any) -> str:
    if isinstance(message, ContextMessage):
        return message.role
    if isinstance(message, dict):
        value = message.get("role", message.get("type", ""))
        return value if isinstance(value, str) else ""
    value = getattr(message, "role", getattr(message, "type", ""))
    return value if isinstance(value, str) else ""


def _message_content(message: Any) -> str:
    if isinstance(message, ContextMessage):
        return message.content
    if isinstance(message, dict):
        value = message.get("content", "")
        return value if isinstance(value, str) else str(value)
    value = getattr(message, "content", "")
    return value if isinstance(value, str) else str(value)


def compute_breakpoint(messages: Sequence[Any], keep_recent: int = 3) -> int:
    """Return the first index of the recent tool-call window.

    Everything before this index is the cache-stable prefix. If the conversation has fewer than
    keep_recent tool messages, the entire input remains in the stable prefix.
    """

    if keep_recent < 1:
        raise ValueError("keep_recent must be at least 1")
    tool_indices = [
        index for index, message in enumerate(messages) if _message_role(message) == "tool"
    ]
    if len(tool_indices) <= keep_recent:
        return len(messages)
    return tool_indices[-keep_recent]


def compress_after_breakpoint(
    messages: Sequence[Any],
    breakpoint_idx: int,
    *,
    max_tool_chars: int = 2_000,
) -> list[Any]:
    """Bound oversized tool observations without changing the cache-stable prefix."""

    if max_tool_chars < 1:
        raise ValueError("max_tool_chars must be positive")
    boundary = max(0, min(len(messages), breakpoint_idx))
    result = list(messages[:boundary])
    suffix = "\n[...工具结果已精简]"
    for message in messages[boundary:]:
        content = _message_content(message)
        if _message_role(message) != "tool" or len(content) <= max_tool_chars:
            result.append(message)
            continue
        bounded = content[: max(0, max_tool_chars - len(suffix))] + suffix
        if isinstance(message, ContextMessage):
            result.append(ContextMessage(role=message.role, content=bounded))
        elif isinstance(message, dict):
            result.append({**message, "content": bounded})
        else:
            result.append(message)
    return result


__all__ = [
    "CacheBreakpoint",
    "ClarificationContext",
    "ContextCompressionSettings",
    "ContextMessage",
    "ContextPreferenceSource",
    "compress_after_breakpoint",
    "compute_breakpoint",
]
