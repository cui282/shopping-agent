from __future__ import annotations

import pytest

from app.agent import main_agent
from app.harness.drift_detector import detect_drift
from app.harness.middleware import HarnessMiddleware, HookRejectSignal, harness
from app.harness.phase import Phase, PhaseStateMachine
from app.harness.step_validation import check_schema, check_sequencing
from app.schemas import ShoppingPlan, TaskRequest
from app.utils.thread_ctx import thread_scope


@pytest.mark.asyncio
async def test_harness_runs_hooks_by_priority_and_fails_open_for_optional_errors() -> None:
    middleware = HarnessMiddleware()
    order: list[str] = []

    async def late(context):
        order.append("late")
        return {"value": context["value"] + 1}

    async def early(context):
        order.append("early")
        return {"value": context["value"] + 1}

    async def broken(_context):
        raise RuntimeError("optional")

    middleware.register("pre_think", "late", late, priority=20)
    middleware.register("pre_think", "early", early, priority=10)
    middleware.register("pre_think", "broken", broken, priority=30)

    result = await middleware.run("pre_think", {"value": 0})
    assert order == ["early", "late"]
    assert result["value"] == 2
    assert result["hook_errors"][0]["name"] == "broken"


@pytest.mark.asyncio
async def test_harness_rejection_is_explicit() -> None:
    middleware = HarnessMiddleware()

    async def reject(_context):
        raise HookRejectSignal("blocked")

    middleware.register("pre_tool_call", "reject", reject)
    with pytest.raises(HookRejectSignal, match="blocked"):
        await middleware.run("pre_tool_call", {"tool_name": "unknown"})


def test_phase_machine_exposes_dynamic_permissions_and_transitions() -> None:
    machine = PhaseStateMachine()
    machine.reset()
    assert machine.get_current_phase() == Phase.PLANNING
    assert machine.is_tool_allowed("planner")
    machine.observe_tool("planner")
    assert machine.get_current_phase() == Phase.SEARCHING
    assert machine.is_tool_allowed("item_search")
    machine.observe_tool("recall")
    assert machine.get_current_phase() == Phase.COMPARING
    machine.observe_tool("shipping_calc")
    assert machine.get_current_phase() == Phase.CONCLUDING
    assert not machine.is_tool_allowed("item_search")


@pytest.mark.asyncio
async def test_step_assertions_and_drift_are_advisory() -> None:
    plan = ShoppingPlan(category="耳机")
    schema = await check_schema({"tool_result": plan})
    assert schema is None
    sequence = await check_sequencing({"tool_name": "price_compare", "tool_history": ()})
    assert sequence["assertions_failed"][0]["type"] == "sequencing"
    drift = detect_drift(
        {
            "query": "旅行三件套",
            "tool_history": ("item_search",) * 4,
            "tool_result": "露营灯",
            "preferences": {"avoid": ["塑料"]},
        }
    )
    assert drift and drift["drift"]["detected"]


@pytest.mark.asyncio
async def test_run_agent_executes_session_lifecycle_hooks(monkeypatch, tmp_path) -> None:
    events: list[str] = []

    async def fake_impl(*_args, **_kwargs):
        return "completed"

    async def on_start(_context):
        events.append("start")

    async def pre_think(_context):
        events.append("think")

    async def on_end(_context):
        events.append("end")

    monkeypatch.setattr(main_agent, "_run_agent_impl", fake_impl)
    harness.register("on_session_start", "test_start", on_start, priority=1)
    harness.register("pre_think", "test_think", pre_think, priority=1)
    harness.register("on_session_end", "test_end", on_end, priority=1)
    try:
        with thread_scope("thread-1", tmp_path):
            result = await main_agent.run_agent(
                TaskRequest(query="耳机", user_id="user-1"), None, None
            )
    finally:
        harness.clear("on_session_start")
        harness.clear("pre_think")
        harness.clear("on_session_end")
    assert result == "completed"
    assert events == ["start", "think", "end"]
