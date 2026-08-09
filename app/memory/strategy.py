"""Reviewed strategy and lesson memory, kept separate from explicit user preferences."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from pydantic import Field

from app.schemas import StrictModel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StrategyEntry(StrictModel):
    strategy_id: str = Field(pattern=r"^strat-[A-Za-z0-9_-]{4,80}$")
    query_pattern: str = Field(min_length=1, max_length=400)
    summary: str = Field(min_length=1, max_length=2000)
    key_decisions: list[str] = Field(default_factory=list, max_length=16)
    tool_hints: list[str] = Field(default_factory=list, max_length=16)
    rubric_score: float = Field(ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    use_count: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=_now)
    last_used_at: str | None = None


class LessonEntry(StrictModel):
    lesson_id: str = Field(pattern=r"^lesson-[A-Za-z0-9_-]{4,80}$")
    query_pattern: str = Field(min_length=1, max_length=400)
    failure: str = Field(min_length=1, max_length=1000)
    prevention: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    created_at: str = Field(default_factory=_now)


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", value)}


class StrategyLibrary:
    """Small process-local library for reviewed strategies and lessons.

    A durable deployment may wrap this API with Redis or a vector store. The default deliberately
    keeps strategy learning opt-in and never mutates user preferences implicitly.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyEntry] = {}
        self._lessons: dict[str, LessonEntry] = {}

    def put_strategy(self, entry: StrategyEntry) -> None:
        self._strategies[entry.strategy_id] = entry

    def put_lesson(self, entry: LessonEntry) -> None:
        self._lessons[entry.lesson_id] = entry

    def read_relevant_strategies(self, query: str, *, top_k: int = 3) -> list[StrategyEntry]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query_tokens = _tokens(query)
        ranked = sorted(
            self._strategies.values(),
            key=lambda entry: (
                -(
                    len(query_tokens & _tokens(entry.query_pattern))
                    / max(1, len(query_tokens))
                    * entry.confidence
                ),
                -entry.rubric_score,
                entry.strategy_id,
            ),
        )
        return [entry for entry in ranked if entry.confidence >= 0.3][:top_k]

    def read_relevant_lessons(self, query: str, *, top_k: int = 3) -> list[LessonEntry]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query_tokens = _tokens(query)
        ranked = sorted(
            self._lessons.values(),
            key=lambda entry: -len(query_tokens & _tokens(entry.query_pattern)) * entry.confidence,
        )
        return [entry for entry in ranked if entry.confidence >= 0.3][:top_k]

    def record_use(self, strategy_id: str) -> StrategyEntry:
        entry = self._strategies[strategy_id]
        updated = entry.model_copy(
            update={
                "use_count": entry.use_count + 1,
                "confidence": min(1.0, entry.confidence + 0.02),
                "last_used_at": _now(),
            }
        )
        self._strategies[strategy_id] = updated
        return updated

    def decay(self, factor: float = 0.98) -> None:
        if not 0 < factor <= 1 or not math.isfinite(factor):
            raise ValueError("decay factor must be finite and in (0, 1]")
        for key, entry in self._strategies.items():
            self._strategies[key] = entry.model_copy(
                update={"confidence": round(entry.confidence * factor, 6)}
            )

    def strategies(self) -> tuple[StrategyEntry, ...]:
        return tuple(self._strategies.values())

    def lessons(self) -> tuple[LessonEntry, ...]:
        return tuple(self._lessons.values())


strategy_library = StrategyLibrary()


__all__ = ["LessonEntry", "StrategyEntry", "StrategyLibrary", "strategy_library"]
