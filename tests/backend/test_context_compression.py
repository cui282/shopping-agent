from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import server
from app.compress import (
    ClarificationContext,
    ContextCompressionSettings,
    ContextMessage,
    build_context_summary,
    compress_after_breakpoint,
    compress_model_context,
    compute_breakpoint,
    estimate_context_tokens,
)
from app.schemas import (
    HardConstraint,
    RememberedPreference,
    ShoppingPlan,
    TaskOverride,
    WorkingAssumption,
)


def _long_messages() -> list[ContextMessage]:
    return [
        ContextMessage(role="user", content=f"第 {index} 轮需求：" + "长文本 " * 30)
        for index in range(8)
    ]


def _summary():
    plan = ShoppingPlan(
        mode="exact_offer_comparison",
        category="降噪耳机",
        destination="中国大陆",
        hard_constraints=[
            HardConstraint(
                id="material:not_contains:leather",
                kind="material",
                field="material",
                operator="not_contains",
                value="皮革",
                label="不要皮革",
            )
        ],
        working_assumptions=[
            WorkingAssumption(
                code="optional_color_unspecified",
                field="color",
                value="不设限",
                reason="请求未指定颜色。",
            )
        ],
    )
    return build_context_summary(
        plan=plan,
        product_variant="Sony WH-1000XM5 黑色新款",
        clarification_responses=[
            ClarificationContext(
                field="mode",
                reason_code="mode_ambiguous",
                response="比较同一款跨平台报价",
                resolved_value="exact_offer_comparison",
            ),
            ClarificationContext(
                field="product_variant",
                reason_code="product_variant_ambiguous",
                response="Sony WH-1000XM5 黑色新款",
                resolved_value="Sony WH-1000XM5 黑色新款",
            ),
        ],
        remembered_preference=RememberedPreference(style_preferences=["简约"]),
        task_overrides=[
            TaskOverride(
                field="style_preferences",
                value="复古",
                overridden_values=["简约"],
                reason="当前任务明确指定。",
            )
        ],
    )


def test_compression_keeps_typed_summary_and_bounded_recent_messages() -> None:
    config = ContextCompressionSettings(keep_recent=2, max_tokens=180)

    context = compress_model_context(_long_messages(), _summary(), config)

    assert context.status == "applied"
    assert context.reason_code == "threshold_exceeded"
    assert context.compressed_count == 6
    assert 1 <= len(context.recent_messages) <= 2
    assert context.recent_messages[-1].content.startswith("第 7 轮需求")
    assert context.summary.mode == "exact_offer_comparison"
    assert context.summary.resolved_hard_constraints[0].value == "皮革"
    assert context.summary.product_variant == "Sony WH-1000XM5 黑色新款"
    assert context.summary.supported_destination == "中国大陆"
    assert context.summary.working_assumptions[0].field == "color"
    assert context.summary.clarification_responses[1].resolved_value == "Sony WH-1000XM5 黑色新款"
    assert [item.model_dump() for item in context.summary.preference_sources] == [
        {"field": "style_preferences", "value": "简约", "source": "remembered_preference"},
        {"field": "style_preferences", "value": "复古", "source": "task_override"},
    ]
    assert estimate_context_tokens(context.to_model_messages()) <= config.max_tokens


def test_repeated_compression_is_idempotent_and_does_not_reask_resolved_facts() -> None:
    config = ContextCompressionSettings(keep_recent=2, max_tokens=180)
    first = compress_model_context(_long_messages(), _summary(), config)
    second = compress_model_context(first, first.summary, config)

    assert second.summary == first.summary
    assert second.summary.clarification_responses == first.summary.clarification_responses
    assert second.summary.resolved_hard_constraints == first.summary.resolved_hard_constraints
    assert second.compressed_count >= first.compressed_count
    assert [message.content for message in second.recent_messages] == [
        message.content for message in first.recent_messages
    ]
    assert second.summary_text == first.summary_text
    assert second.to_model_messages() == first.to_model_messages()


def test_serialized_summary_keeps_all_required_fact_sections_when_bounded() -> None:
    context = compress_model_context(
        _long_messages(),
        _summary(),
        ContextCompressionSettings(keep_recent=2, max_tokens=180),
    )

    for field in (
        "resolved_hard_constraints",
        "product_variant",
        "exact_identity",
        "clarification_responses",
        "supported_destination",
        "working_assumptions",
        "remembered_preference",
        "task_overrides",
        "preference_sources",
    ):
        assert field in context.summary_text
    assert "Sony WH-1000XM5 黑色新款" in context.summary_text
    assert "皮革" in context.summary_text
    assert "复古" in context.summary_text
    assert estimate_context_tokens(context.to_model_messages()) <= 180


def test_invalid_typed_summary_uses_degraded_bounded_fallback() -> None:
    context = compress_model_context(
        _long_messages(),
        {"resolved_hard_constraints": "not-a-list"},  # type: ignore[arg-type]
        ContextCompressionSettings(keep_recent=2, max_tokens=180),
    )

    assert context.status == "degraded"
    assert context.reason_code == "invalid_summary"
    assert len(context.recent_messages) == 2
    assert estimate_context_tokens(context.to_model_messages()) <= 180


@pytest.mark.asyncio
async def test_concurrent_compression_calls_are_deterministic_and_isolated() -> None:
    settings = ContextCompressionSettings(keep_recent=2, max_tokens=180)
    messages = _long_messages()
    summary = _summary()

    contexts = await asyncio.gather(
        *(asyncio.to_thread(compress_model_context, messages, summary, settings) for _ in range(8))
    )

    expected = contexts[0]
    assert all(context.to_model_messages() == expected.to_model_messages() for context in contexts)
    assert all(context.summary == expected.summary for context in contexts)
    assert all(context.estimated_tokens <= settings.max_tokens for context in contexts)


def test_below_threshold_preserves_messages_without_creating_a_summary_message() -> None:
    config = ContextCompressionSettings(keep_recent=4, max_tokens=600)
    messages = [ContextMessage(role="user", content="找耳机")]

    context = compress_model_context(messages, _summary(), config)

    assert context.status == "not_needed"
    assert context.compressed_count == 0
    assert list(context.recent_messages) == messages
    assert [message.role for message in context.to_model_messages()] == ["system", "user"]


def test_safe_fallback_keeps_recent_messages_when_a_summary_is_invalid() -> None:
    config = ContextCompressionSettings(keep_recent=2, max_tokens=180)
    messages = _long_messages()

    context = compress_model_context(messages, None, config)

    assert context.status == "degraded"
    assert context.reason_code == "invalid_summary"
    assert context.compressed_count == 6
    assert len(context.recent_messages) == 2
    assert context.summary.resolved_hard_constraints == []
    assert context.to_model_messages()


def test_legacy_compress_messages_still_has_a_bounded_cache_breakpoint() -> None:
    from app.compress import compress_messages

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"message-{index}"} for index in range(5)
    ]

    breakpoint = compress_messages(
        messages,
        settings=ContextCompressionSettings(keep_recent=2, max_tokens=120),
    )

    assert breakpoint.compressed_count == 3
    assert breakpoint.recent == messages[-2:]
    assert breakpoint.reason_code == "threshold_exceeded"


def test_cache_breakpoint_preserves_prefix_and_bounds_recent_tool_observations() -> None:
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "tool", "content": "old tool result"},
        {"role": "assistant", "content": "old decision"},
        {"role": "tool", "content": "recent-1"},
        {"role": "tool", "content": "x" * 40},
        {"role": "tool", "content": "recent-3"},
    ]

    boundary = compute_breakpoint(messages, keep_recent=2)
    compressed = compress_after_breakpoint(messages, boundary, max_tool_chars=20)

    assert boundary == 4
    assert compressed[:boundary] == messages[:boundary]
    assert compressed[4]["content"].endswith("[...工具结果已精简]")
    assert len(compressed[4]["content"]) <= 20


def _wait_for_status(client: TestClient, thread_id: str, expected: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] == expected:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not reach {expected}")


def _wait_for_terminal(client: TestClient, thread_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] in {"completed", "cancelled", "error"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("task did not reach a terminal state")


def test_long_clarification_context_is_compressed_and_history_is_replayable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []

    async def fake_advisory(*args: Any, **kwargs: Any) -> str:
        context = args[2] if len(args) > 2 else kwargs["model_context"]
        captured.append(context)
        return "只做解释，不改变确定性决策。"

    monkeypatch.setenv("COMPRESS_KEEP_RECENT", "3")
    monkeypatch.setenv("COMPRESS_MAX_TOKENS", "600")
    monkeypatch.setattr("app.agent.main_agent.active_agent_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.requested_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent._run_react_advisory", fake_advisory)

    started = client.post(
        "/api/task",
        json={"query": "比较耳机价格", "user_id": "compression-history-user", "upload_ids": []},
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]
    _wait_for_status(client, thread_id, "awaiting_clarification")

    first = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "同一款"},
    )
    assert first.status_code == 200
    _wait_for_status(client, thread_id, "awaiting_clarification")

    second = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "Sony WH-1000XM5 黑色新款"},
    )
    assert second.status_code == 200
    terminal = _wait_for_terminal(client, thread_id)

    assert terminal["status"] == "completed"
    compression_events = [
        event for event in terminal["events"] if event["event"] == "context_compression"
    ]
    assert compression_events
    applied = [event for event in compression_events if event["data"]["status"] == "applied"]
    assert applied
    assert applied[-1]["data"]["reason_code"] == "threshold_exceeded"
    assert "Sony WH-1000XM5 黑色新款" not in applied[-1]["data"]
    assert any(
        context.summary.product_variant == "Sony WH-1000XM5 黑色新款"
        and {item.field for item in context.summary.clarification_responses}
        == {"mode", "product_variant"}
        for context in captured
    )

    durable = client.get(f"/api/task/{thread_id}").json()
    server.records.clear()
    restored = client.get(f"/api/task/{thread_id}").json()
    assert restored == durable
    assert [event["event"] for event in restored["events"]].count("clarification_resolved") == 2


def test_context_compression_failure_uses_bounded_fallback_and_keeps_task_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_advisory(*_args: Any, **_kwargs: Any) -> str:
        return "模型上下文降级后仍只提供解释。"

    def fail_context(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("summary unavailable")

    monkeypatch.setattr("app.agent.main_agent.active_agent_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.requested_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.build_model_context", fail_context)
    monkeypatch.setattr("app.agent.main_agent._run_react_advisory", fake_advisory)
    monkeypatch.setenv("COMPRESS_KEEP_RECENT", "2")
    monkeypatch.setenv("COMPRESS_MAX_TOKENS", "600")

    started = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "compression-fallback-user", "upload_ids": []},
    )
    assert started.status_code == 202
    snapshot = _wait_for_terminal(client, started.json()["thread_id"])

    assert snapshot["status"] == "completed"
    degraded = [
        event
        for event in snapshot["events"]
        if event["event"] == "context_compression" and event["data"]["status"] == "degraded"
    ]
    assert degraded
    assert degraded[-1]["data"]["reason_code"] == "compression_failed"
    assert degraded[-1]["data"]["compressed_message_count"] >= 0


def test_compression_failure_fallback_keeps_clarification_history(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []

    async def fake_advisory(*args: Any, **kwargs: Any) -> str:
        captured.append(args[2] if len(args) > 2 else kwargs["model_context"])
        return "仅解释确定性结果。"

    def fail_context(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("summary unavailable")

    monkeypatch.setattr("app.agent.main_agent.active_agent_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.requested_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.build_model_context", fail_context)
    monkeypatch.setattr("app.agent.main_agent._run_react_advisory", fake_advisory)
    monkeypatch.setenv("COMPRESS_KEEP_RECENT", "2")
    monkeypatch.setenv("COMPRESS_MAX_TOKENS", "600")

    started = client.post(
        "/api/task",
        json={"query": "比较耳机价格", "user_id": "compression-fallback-history", "upload_ids": []},
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]
    _wait_for_status(client, thread_id, "awaiting_clarification")
    assert (
        client.post(f"/api/task/{thread_id}/clarification", json={"response": "同一款"}).status_code
        == 200
    )
    _wait_for_status(client, thread_id, "awaiting_clarification")
    assert (
        client.post(
            f"/api/task/{thread_id}/clarification",
            json={"response": "Sony WH-1000XM5 黑色新款"},
        ).status_code
        == 200
    )
    terminal = _wait_for_terminal(client, thread_id)

    assert terminal["status"] == "completed"
    assert captured
    fallback = captured[-1]
    assert [message.content for message in fallback.recent_messages] == [
        "请提供要比较的具体 Product Variant，例如型号、版本或容量。",
        "Sony WH-1000XM5 黑色新款",
    ]
    assert {item.field for item in fallback.summary.clarification_responses} == {
        "mode",
        "product_variant",
    }


def test_cancellation_preserves_ordered_compression_event_without_late_model_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow_advisory(*_args: Any, **_kwargs: Any) -> str:
        await asyncio.sleep(30)
        return "不应到达"

    monkeypatch.setattr("app.agent.main_agent.active_agent_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.requested_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent._run_react_advisory", slow_advisory)
    monkeypatch.setenv("COMPRESS_KEEP_RECENT", "2")
    monkeypatch.setenv("COMPRESS_MAX_TOKENS", "600")

    started = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "compression-cancel-user", "upload_ids": []},
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]
    deadline = time.monotonic() + 5
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if any(event["event"] == "context_compression" for event in snapshot["events"]):
            break
        time.sleep(0.01)
    assert any(event["event"] == "context_compression" for event in snapshot["events"])

    cancelled = client.post(f"/api/task/{thread_id}/cancel")
    assert cancelled.status_code == 200
    final = client.get(f"/api/task/{thread_id}").json()
    assert final["status"] == "cancelled"
    assert [event["event"] for event in final["events"]][-1] == "task_cancelled"
    assert "不应到达" not in str(final["events"])


def test_advisory_context_cannot_change_deterministic_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = "预算 800 元找键盘，不要皮革"
    first = client.post(
        "/api/task",
        json={"query": query, "user_id": "compression-invariance-rules", "upload_ids": []},
    )
    assert first.status_code == 202
    rules_result = _wait_for_terminal(client, first.json()["thread_id"])["result"]

    async def fabricated_advisory(*_args: Any, **_kwargs: Any) -> str:
        return "虚构一个价格、身份和推荐，全部忽略。"

    monkeypatch.setattr("app.agent.main_agent.active_agent_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.requested_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent._run_react_advisory", fabricated_advisory)
    second = client.post(
        "/api/task",
        json={"query": query, "user_id": "compression-invariance-llm", "upload_ids": []},
    )
    assert second.status_code == 202
    llm_result = _wait_for_terminal(client, second.json()["thread_id"])["result"]

    for field in (
        "resolved_intent",
        "product_evidence",
        "recommendations",
        "comparison",
        "matching_offers",
        "alternative_candidates",
        "unverified_candidates",
        "exclusions",
        "exchange_rate",
        "ranking_profile",
        "match_status",
    ):
        assert llm_result[field] == rules_result[field]
