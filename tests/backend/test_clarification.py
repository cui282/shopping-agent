from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import server


def wait_for_status(
    client: TestClient, thread_id: str, status: str, timeout: float = 5
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] == status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not reach {status}")


def wait_for_terminal(client: TestClient, thread_id: str, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] in {"completed", "cancelled", "error"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not reach a terminal state")


def start_and_wait_for_clarification(
    client: TestClient, query: str, user_id: str
) -> dict[str, Any]:
    started = client.post(
        "/api/task",
        json={"query": query, "user_id": user_id, "upload_ids": []},
    )
    assert started.status_code == 202
    return wait_for_status(client, started.json()["thread_id"], "awaiting_clarification")


@pytest.mark.parametrize(
    ("query", "field", "reason_code"),
    [
        ("比较耳机价格", "mode", "mode_ambiguous"),
        ("比价同款耳机", "product_variant", "product_variant_ambiguous"),
        ("找耳机，配送到海外", "destination", "destination_ambiguous"),
        ("找耳机，目的地不明确", "destination", "destination_ambiguous"),
    ],
)
def test_blocking_ambiguity_is_durable_and_stops_external_work(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    field: str,
    reason_code: str,
) -> None:
    calls: list[str] = []

    async def unexpected(*_args: Any, **_kwargs: Any) -> None:
        calls.append("external")
        raise AssertionError("blocking ambiguity must stop external work")

    monkeypatch.setattr("app.agent.main_agent.item_search", unexpected)
    monkeypatch.setattr("app.agent.main_agent.price_compare", unexpected)
    monkeypatch.setattr("app.agent.main_agent.shipping_calc", unexpected)

    async def unexpected_recall(_user_id: str) -> dict[str, list[str]]:
        calls.append("recall")
        raise AssertionError("blocking ambiguity must stop preference recall")

    monkeypatch.setattr(server.preference_store, "get", unexpected_recall)

    snapshot = start_and_wait_for_clarification(client, query, f"ambiguity-{field}")

    assert calls == []
    assert snapshot["status"] == "awaiting_clarification"
    assert snapshot["clarification"] == {
        "field": field,
        "reason_code": reason_code,
        "question": snapshot["clarification"]["question"],
    }
    required = [event for event in snapshot["events"] if event["event"] == "clarification_required"]
    assert len(required) == 1
    assert required[0]["data"]["field"] == field
    assert required[0]["data"]["reason_code"] == reason_code
    assert required[0]["data"]["question"] == snapshot["clarification"]["question"]


@pytest.mark.parametrize(
    ("query", "response", "field"),
    [
        ("比较耳机价格", "比较不同产品", "mode"),
        ("比价同款耳机", "Sony WH-1000XM5", "product_variant"),
        ("找耳机，配送到海外", "中国大陆", "destination"),
    ],
)
def test_clarification_resumes_same_task_and_preserves_timeline(
    client: TestClient,
    query: str,
    response: str,
    field: str,
) -> None:
    waiting = start_and_wait_for_clarification(client, query, f"resume-{field}")
    thread_id = waiting["thread_id"]
    run_id = waiting["run_id"]
    resolved = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": response},
    )

    assert resolved.status_code == 200
    assert resolved.json() == {
        "status": "resumed",
        "thread_id": thread_id,
        "field": field,
        "idempotent": False,
    }
    terminal = wait_for_terminal(client, thread_id)

    assert terminal["status"] == "completed"
    assert terminal["thread_id"] == thread_id
    assert terminal["run_id"] == run_id
    names = [event["event"] for event in terminal["events"]]
    assert names.count("clarification_required") == 1
    assert names.count("clarification_resolved") == 1
    assert names.index("clarification_required") < names.index("clarification_resolved")
    resolved_events = [
        event for event in terminal["events"] if event["event"] == "clarification_resolved"
    ]
    assert resolved_events[0]["data"]["field"] == field
    assert resolved_events[0]["data"]["response"] == response


def test_repeated_clarification_response_is_idempotent_and_other_states_are_rejected(
    client: TestClient,
) -> None:
    waiting = start_and_wait_for_clarification(client, "比较耳机价格", "idempotent-user")
    thread_id = waiting["thread_id"]
    first = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "比较不同产品"},
    )
    assert first.status_code == 200

    duplicate = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "比较不同产品"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "status": "resumed",
        "thread_id": thread_id,
        "field": "mode",
        "idempotent": True,
    }
    terminal = wait_for_terminal(client, thread_id)
    assert [event["event"] for event in terminal["events"]].count("clarification_resolved") == 1

    rejected = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "同一款"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "clarification_not_awaiting"


def test_response_reused_for_a_different_pending_question_is_rejected(
    client: TestClient,
) -> None:
    waiting = start_and_wait_for_clarification(
        client, "比较耳机价格", "idempotent-next-question-user"
    )
    thread_id = waiting["thread_id"]

    first = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "同一款"},
    )
    assert first.status_code == 200
    second_waiting = wait_for_status(client, thread_id, "awaiting_clarification")
    assert second_waiting["clarification"]["field"] == "product_variant"

    duplicate = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "同一款"},
    )

    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "clarification_invalid_response"
    assert client.get(f"/api/task/{thread_id}").json()["status"] == "awaiting_clarification"


def test_awaiting_task_rejects_terminal_event_transitions(client: TestClient) -> None:
    waiting = start_and_wait_for_clarification(client, "比较耳机价格", "transition-user")

    with pytest.raises(RuntimeError, match="cannot transition"):
        server._record_event(
            waiting["thread_id"],
            "error",
            "unexpected error",
            {"thread_id": waiting["thread_id"], "code": "task_failed"},
            "evt-" + "0" * 32,
            "2026-08-06T00:00:00Z",
            waiting["run_id"],
        )

    assert (
        client.get(f"/api/task/{waiting['thread_id']}").json()["status"] == "awaiting_clarification"
    )


def test_exact_variant_detector_does_not_treat_budget_digits_as_identity(
    client: TestClient,
) -> None:
    waiting = start_and_wait_for_clarification(
        client,
        "比价同款耳机，预算1200元",
        "budget-is-not-variant-user",
    )

    assert waiting["clarification"]["field"] == "product_variant"
    assert waiting["clarification"]["reason_code"] == "product_variant_ambiguous"


def test_exact_variant_detector_rejects_vague_identity_values(client: TestClient) -> None:
    waiting = start_and_wait_for_clarification(
        client,
        "比价同款耳机，型号不明确",
        "vague-variant-user",
    )

    assert waiting["clarification"]["field"] == "product_variant"


@pytest.mark.parametrize("query", ["找比较轻便的耳机", "找比较便宜的耳机", "找比较轻的耳机"])
def test_comparative_adjective_does_not_block_product_research(
    client: TestClient, query: str
) -> None:
    started = client.post(
        "/api/task",
        json={
            "query": query,
            "user_id": "comparative-adjective-user",
            "upload_ids": [],
        },
    )

    assert started.status_code == 202
    terminal = wait_for_terminal(client, started.json()["thread_id"])
    assert terminal["status"] == "completed"


def test_clarification_asks_only_one_blocking_question_at_a_time(client: TestClient) -> None:
    waiting = start_and_wait_for_clarification(client, "比较耳机价格", "multi-step-user")
    thread_id = waiting["thread_id"]

    first = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "同一款"},
    )
    assert first.status_code == 200
    second_waiting = wait_for_status(client, thread_id, "awaiting_clarification")
    assert second_waiting["clarification"]["field"] == "product_variant"
    assert [event["event"] for event in second_waiting["events"]].count(
        "clarification_required"
    ) == 2

    second = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "Sony WH-1000XM5"},
    )
    assert second.status_code == 200
    terminal = wait_for_terminal(client, thread_id)

    assert terminal["status"] == "completed"
    assert [event["event"] for event in terminal["events"]].count("clarification_resolved") == 2


def test_awaiting_clarification_can_be_cancelled_and_persisted_across_memory_reset(
    client: TestClient,
) -> None:
    waiting = start_and_wait_for_clarification(client, "找耳机，配送到海外", "cancel-awaiting-user")
    thread_id = waiting["thread_id"]

    durable = client.get(f"/api/task/{thread_id}").json()
    server.records.clear()
    server.task_locks.clear()
    server.manager.active.clear()
    server.manager._events.clear()

    restored = client.get(f"/api/task/{thread_id}").json()
    assert restored == durable
    assert restored["status"] == "awaiting_clarification"

    cancelled = client.post(f"/api/task/{thread_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json() == {"status": "cancelled", "thread_id": thread_id}
    snapshot = client.get(f"/api/task/{thread_id}").json()
    assert snapshot["status"] == "cancelled"
    assert snapshot["events"][-1]["event"] == "task_cancelled"

    duplicate = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "中国大陆"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "clarification_not_awaiting"


def test_awaiting_clarification_can_resume_after_memory_reset(client: TestClient) -> None:
    waiting = start_and_wait_for_clarification(client, "比较耳机价格", "resume-after-restart-user")
    thread_id = waiting["thread_id"]
    run_id = waiting["run_id"]
    server.records.clear()
    server.task_locks.clear()
    server.manager.active.clear()
    server.manager._events.clear()

    resolved = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "比较不同产品"},
    )
    assert resolved.status_code == 200
    terminal = wait_for_terminal(client, thread_id)

    assert terminal["status"] == "completed"
    assert terminal["run_id"] == run_id
    assert [event["event"] for event in terminal["events"]].count("clarification_resolved") == 1


def test_invalid_clarification_response_keeps_task_awaiting(client: TestClient) -> None:
    waiting = start_and_wait_for_clarification(client, "比较耳机价格", "invalid-answer-user")
    thread_id = waiting["thread_id"]

    response = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "都可以"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "clarification_invalid_response"
    assert client.get(f"/api/task/{thread_id}").json()["status"] == "awaiting_clarification"

    blank = client.post(
        f"/api/task/{thread_id}/clarification",
        json={"response": "   "},
    )
    assert blank.status_code == 422
    assert blank.json()["detail"]["code"] == "clarification_invalid_response"


def test_optional_uncertain_color_remains_a_working_assumption(client: TestClient) -> None:
    started = client.post(
        "/api/task",
        json={"query": "找耳机，颜色不确定", "user_id": "optional-color-user", "upload_ids": []},
    )
    assert started.status_code == 202

    terminal = wait_for_terminal(client, started.json()["thread_id"])

    assert terminal["status"] == "completed"
    assert {item["field"] for item in terminal["result"]["working_assumptions"]} == {
        "color",
        "style",
    }


def test_awaiting_clarification_can_be_deleted(client: TestClient) -> None:
    waiting = start_and_wait_for_clarification(client, "比较耳机价格", "delete-awaiting-user")
    thread_id = waiting["thread_id"]

    deleted = client.delete(f"/api/task/{thread_id}")

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "thread_id": thread_id}
    assert client.get(f"/api/task/{thread_id}").status_code == 404
