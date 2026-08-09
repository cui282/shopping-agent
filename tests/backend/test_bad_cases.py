from __future__ import annotations

import pytest

from app.evolution.bad_cases import BadCaseLedger, capture_bad_case


def test_bad_case_capture_is_review_only_and_thresholded() -> None:
    assert (
        capture_bad_case(
            trace_id="trace-1",
            query="旅行三件套",
            reason="忽略预算",
            rubric_score=0.8,
        )
        is None
    )
    case = capture_bad_case(
        trace_id="trace-1",
        query="旅行三件套",
        reason="忽略预算",
        rubric_score=0.35,
        tool_sequence=["planner", "item_search"],
    )
    assert case is not None
    assert case.severity == "P0"
    assert case.status == "new"
    assert case.tool_sequence == ["planner", "item_search"]


def test_bad_case_ledger_requires_explicit_human_review() -> None:
    case = capture_bad_case(
        trace_id="trace-2",
        query="耳机",
        reason="无证据",
        rubric_score=0.6,
    )
    assert case is not None
    ledger = BadCaseLedger()
    ledger.add(case)
    with pytest.raises(KeyError):
        ledger.review("bad-missing", status="rejected", reviewer="human")
    reviewed = ledger.review(
        case.bad_case_id,
        status="accepted",
        reviewer="human",
        notes="补充数据通道后重试",
    )
    assert reviewed.status == "accepted"
    assert ledger.list(status="accepted") == (reviewed,)
