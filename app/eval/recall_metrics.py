"""Small, deterministic retrieval metrics and release gates for CategoryInsight."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from pydantic import Field

from app.schemas import StrictModel


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _validate_k(k: int) -> None:
    if k < 1:
        raise ValueError("k must be positive")


def recall_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Fraction of unique relevant cards covered by the first ``k`` results."""

    _validate_k(k)
    rel = set(_unique(relevant))
    if not rel:
        return 0.0
    return len(set(_unique(retrieved)[:k]) & rel) / len(rel)


def mrr(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    """Reciprocal rank of the first relevant card."""

    rel = set(_unique(relevant))
    for index, card_id in enumerate(_unique(retrieved), start=1):
        if card_id in rel:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """NDCG@K using the operator-provided order as graded relevance."""

    _validate_k(k)
    ordered_relevant = _unique(relevant)
    if not ordered_relevant:
        return 0.0
    gains = {
        card_id: len(ordered_relevant) - index for index, card_id in enumerate(ordered_relevant)
    }
    actual = sum(
        gains.get(card_id, 0) / math.log2(index + 2)
        for index, card_id in enumerate(_unique(retrieved)[:k])
    )
    ideal = sum(
        gains[card_id] / math.log2(index + 2) for index, card_id in enumerate(ordered_relevant[:k])
    )
    return actual / ideal if ideal else 0.0


class RecallEvaluation(StrictModel):
    sample_count: int = Field(ge=0)
    k: int = Field(ge=1)
    recall_at_k: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_k: float = Field(ge=0, le=1)


class RecallReleaseGate(StrictModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    evaluation: RecallEvaluation


def evaluate_recall(
    samples: Iterable[tuple[Sequence[str], Sequence[str]]], *, k: int = 10
) -> RecallEvaluation:
    """Average metrics over ``(retrieved, relevant)`` samples."""

    _validate_k(k)
    values = list(samples)
    if not values:
        return RecallEvaluation(sample_count=0, k=k, recall_at_k=0, mrr=0, ndcg_at_k=0)
    count = len(values)
    return RecallEvaluation(
        sample_count=count,
        k=k,
        recall_at_k=sum(recall_at_k(found, expected, k) for found, expected in values) / count,
        mrr=sum(mrr(found, expected) for found, expected in values) / count,
        ndcg_at_k=sum(ndcg_at_k(found, expected, k) for found, expected in values) / count,
    )


def assert_release_gate(
    evaluation: RecallEvaluation,
    *,
    min_recall: float = 0.75,
    min_mrr: float = 0.65,
    min_ndcg: float = 0.70,
) -> RecallReleaseGate:
    """Apply the v1 thresholds from the document without raising in CI callers."""

    thresholds = {
        "recall_at_k": (evaluation.recall_at_k, min_recall),
        "mrr": (evaluation.mrr, min_mrr),
        "ndcg_at_k": (evaluation.ndcg_at_k, min_ndcg),
    }
    failures = [name for name, (actual, minimum) in thresholds.items() if actual < minimum]
    return RecallReleaseGate(passed=not failures, failures=failures, evaluation=evaluation)


__all__ = [
    "RecallEvaluation",
    "RecallReleaseGate",
    "assert_release_gate",
    "evaluate_recall",
    "mrr",
    "ndcg_at_k",
    "recall_at_k",
]
