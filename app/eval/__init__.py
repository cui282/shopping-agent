"""Offline evaluation helpers; no runtime task depends on this package."""

from app.eval.recall_metrics import (
    RecallEvaluation,
    RecallReleaseGate,
    assert_release_gate,
    evaluate_recall,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

__all__ = [
    "RecallEvaluation",
    "RecallReleaseGate",
    "assert_release_gate",
    "evaluate_recall",
    "mrr",
    "ndcg_at_k",
    "recall_at_k",
]
