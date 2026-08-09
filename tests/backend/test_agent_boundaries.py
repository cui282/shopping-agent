from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.budget import (
    TokenBudgetExceeded,
    choose_route,
    record_usage,
    route_model_name,
    start_budget,
)
from app.agent.guard import AgentLoopGuardError, record_tool_call, reset_tool_guard
from app.agent.tool_registry import get_execution_plan, reset_execution_plan, task_tool
from app.utils.thread_ctx import thread_scope


@pytest.mark.asyncio
async def test_task_tool_creates_a_bounded_plan_without_product_evidence(tmp_path: Path) -> None:
    reset_execution_plan()
    with thread_scope("plan-test", tmp_path):
        rendered = await task_tool(
            platforms=["AMAZON", "unknown", "ebay"],
            parallel=False,
            steps=["planner", "task_tool", "unknown"],
            reason="compare configured channels",
        )

    plan = get_execution_plan()
    assert plan is not None
    assert plan.platforms == ["amazon", "ebay"]
    assert plan.fork is False
    assert plan.steps == ["planner"]
    assert plan.source == "model"
    payload = json.loads(rendered)
    assert payload["platforms"] == ["amazon", "ebay"]
    assert "candidates" not in payload


def test_tool_guard_rejects_repeated_calls_and_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LOOP_DETECTION_THRESHOLD", "2")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "3")
    reset_tool_guard()

    record_tool_call("planner", {"query": "headphones"})
    record_tool_call("planner", {"query": "headphones"})
    with pytest.raises(AgentLoopGuardError) as repeated:
        record_tool_call("planner", {"query": "headphones"})
    assert repeated.value.code == "loop_detected"

    reset_tool_guard()
    record_tool_call("a", {})
    record_tool_call("b", {})
    record_tool_call("c", {})
    with pytest.raises(AgentLoopGuardError) as limited:
        record_tool_call("d", {})
    assert limited.value.code == "tool_call_limit"


def test_token_budget_routes_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_LITE_TOKEN_THRESHOLD", "6000")
    monkeypatch.setenv("LLM_MINIMAL_TOKEN_THRESHOLD", "2500")
    start_budget(10_000)

    assert choose_route() == "main"
    record_usage(output_tokens=4_500)
    assert choose_route() == "lite"
    record_usage(output_tokens=3_100)
    assert choose_route() == "minimal"
    with pytest.raises(TokenBudgetExceeded):
        record_usage(output_tokens=3_000)


def test_token_budget_enters_non_llm_fallback_at_five_percent() -> None:
    start_budget(10_000)
    record_usage(output_tokens=9_600)

    assert choose_route() == "fallback"
    assert route_model_name("fallback") == ""
