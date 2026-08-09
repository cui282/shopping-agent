"""Deterministic, review-gated strategy extraction from completed traces."""

from __future__ import annotations

import hashlib

from app.memory.strategy import StrategyEntry


def extract_strategy(
    *,
    query: str,
    tool_sequence: list[str],
    rubric_score: float,
    key_decisions: list[str] | None = None,
    summary: str | None = None,
) -> StrategyEntry | None:
    """Create a strategy only from a high-scoring, non-trivial reviewed trace.

    This is intentionally an offline data-model boundary. It does not call an LLM and does not
    create SFT/RL data or mutate the live preference Store.
    """

    if rubric_score < 0.8 or len(tool_sequence) < 3:
        return None
    normalized = "|".join(tool_sequence)
    strategy_id = f"strat-{hashlib.sha256((query + normalized).encode()).hexdigest()[:12]}"
    decisions = list(key_decisions or [])[:16]
    return StrategyEntry(
        strategy_id=strategy_id,
        query_pattern=query[:400],
        summary=(summary or " → ".join(tool_sequence))[:2000],
        key_decisions=decisions,
        tool_hints=list(dict.fromkeys(tool_sequence))[:16],
        rubric_score=rubric_score,
        confidence=rubric_score,
    )


__all__ = ["extract_strategy"]
