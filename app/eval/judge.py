from __future__ import annotations

from app.eval.rubric import RUBRIC
from app.schemas import ShoppingSummaryOutput


def score_result(result: ShoppingSummaryOutput) -> dict[str, int]:
    scores = {
        "constraint_satisfaction": RUBRIC["constraint_satisfaction"]
        if result.recommendations
        else 10,
        "price_transparency": RUBRIC["price_transparency"] if result.comparison else 0,
        "recommendation_evidence": RUBRIC["recommendation_evidence"]
        if all(item.reason for item in result.recommendations)
        else 0,
        "provider_traceability": RUBRIC["provider_traceability"],
        "conciseness": RUBRIC["conciseness"] if len(result.final_answer) <= 800 else 5,
    }
    scores["total"] = sum(scores.values())
    return scores
