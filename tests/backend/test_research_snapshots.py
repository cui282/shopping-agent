from __future__ import annotations

import asyncio
import importlib
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import server
from app.schemas import Candidate, ShoppingSummaryOutput, TaskSnapshot

price_compare_module = importlib.import_module("app.tools.price_compare")


def _wait_for_terminal(client: TestClient, thread_id: str, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] not in {"running", "awaiting_clarification"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not become terminal")


def _start_completed(
    client: TestClient, query: str = "预算 1200 元，找一款轻便降噪耳机"
) -> dict[str, Any]:
    started = client.post(
        "/api/task",
        json={"query": query, "user_id": "snapshot-user", "upload_ids": []},
    )
    assert started.status_code == 202
    return _wait_for_terminal(client, started.json()["thread_id"])


def test_completed_snapshot_persists_full_contract_and_reopen_is_read_only(
    client: TestClient, monkeypatch
) -> None:
    snapshot = _start_completed(client)
    assert snapshot["status"] == "completed"
    assert snapshot["snapshot_id"] == snapshot["thread_id"]
    assert snapshot["resolved_intent"]["mode"] == "product_research"
    assert snapshot["resolved_intent"]["hard_constraints"]
    assert snapshot.get("working_assumptions", True) is not None
    assert snapshot["applied_preferences"] == {
        "material_preferences": [],
        "style_preferences": [],
        "soft_preferences": [],
        "avoid": [],
    }
    assert "provider_coverage" in snapshot
    assert snapshot["product_evidence"]
    assert "retrieved_at" in snapshot["product_evidence"][0]
    assert "identity_evidence" in snapshot["product_evidence"][0]
    assert snapshot["exchange_rate"]["effective_date"]
    assert {item["name"] for item in snapshot["report_references"]} >= {
        "shopping-report.md",
        "shopping-report.json",
    }
    assert any(event["event"] == "intent_resolved" for event in snapshot["events"])

    before = client.get(f"/api/task/{snapshot['thread_id']}").json()
    calls = {"gateway": 0, "recall": 0, "fx": 0}

    async def no_gateway(*_args, **_kwargs):
        calls["gateway"] += 1
        raise AssertionError("opening a Research Snapshot must not call a gateway")

    def no_recall(*_args, **_kwargs):
        calls["recall"] += 1
        raise AssertionError("opening a Research Snapshot must not recall preferences")

    def no_rates(*_args, **_kwargs):
        calls["fx"] += 1
        raise AssertionError("opening a Research Snapshot must not refresh exchange rates")

    monkeypatch.setattr("app.api.server.run_agent", no_gateway)
    monkeypatch.setattr("app.memory.store.InMemoryPreferenceStore.get", no_recall)
    monkeypatch.setattr(price_compare_module, "_rates", no_rates)

    reopened = client.get(f"/api/research/{snapshot['thread_id']}").json()
    listed = client.get("/api/research", params={"user_id": snapshot["user_id"]}).json()
    assert reopened == before
    assert any(item["thread_id"] == snapshot["thread_id"] for item in listed["snapshots"])
    assert calls == {"gateway": 0, "recall": 0, "fx": 0}


def test_reopening_completed_snapshot_does_not_write_legacy_migrations(
    client: TestClient, monkeypatch
) -> None:
    snapshot = _start_completed(client)
    before = client.get(f"/api/task/{snapshot['thread_id']}").json()

    def fail_persist(*_args, **_kwargs):
        raise AssertionError("opening a Research Snapshot must not write storage")

    monkeypatch.setattr(server, "_persist_snapshot", fail_persist)

    assert client.get(f"/api/research/{snapshot['thread_id']}").json() == before
    assert client.get("/api/research", params={"user_id": snapshot["user_id"]}).status_code == 200


def test_terminal_thread_id_cannot_be_reused(client: TestClient) -> None:
    snapshot = _start_completed(client)
    before = client.get(f"/api/task/{snapshot['thread_id']}").json()

    replacement = client.post(
        "/api/task",
        json={
            "query": "换一个研究请求",
            "thread_id": snapshot["thread_id"],
            "user_id": snapshot["user_id"],
            "upload_ids": [],
        },
    )

    assert replacement.status_code == 409
    assert replacement.json()["detail"]["code"] == "thread_id_immutable"
    assert client.get(f"/api/task/{snapshot['thread_id']}").json() == before


def test_recent_research_is_scoped_to_the_anonymous_shopper_id(client: TestClient) -> None:
    shopper_snapshot = _start_completed(client, "找一款轻便耳机")
    other = client.post(
        "/api/task",
        json={"query": "找一款咖啡机", "user_id": "other-snapshot-user", "upload_ids": []},
    )
    assert other.status_code == 202
    _wait_for_terminal(client, other.json()["thread_id"])

    scoped = client.get("/api/research", params={"user_id": shopper_snapshot["user_id"]})

    assert scoped.status_code == 200
    assert {item["user_id"] for item in scoped.json()["snapshots"]} == {shopper_snapshot["user_id"]}


def test_rerun_requires_the_snapshot_shopper_id(client: TestClient) -> None:
    parent = _start_completed(client)

    response = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": "a-different-shopper", "idempotency_key": "wrong-owner"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "research_snapshot_not_found"


def test_product_evidence_source_must_match_the_result_data_mode(client: TestClient) -> None:
    snapshot = _start_completed(client)
    sandbox_payload = snapshot["result"]
    sandbox_payload["product_evidence"][0]["source"] = "computed"

    with pytest.raises(ValidationError, match="non-fixture Product Evidence"):
        ShoppingSummaryOutput.model_validate(sandbox_payload)

    with pytest.raises(ValidationError, match="non-live Product Evidence"):
        ShoppingSummaryOutput(
            thread_id="thread-live-evidence",
            final_answer="",
            recommendations=[],
            comparison=[],
            files=[],
            provider_mode="live",
            calculation_notice="",
            product_evidence=[
                Candidate(
                    item_id="fixture-item",
                    platform="amazon",
                    title="Fixture item",
                    price=1,
                    currency="USD",
                    source="fixture",
                )
            ],
        )


def test_snapshot_rejects_inconsistent_top_level_result_contract(client: TestClient) -> None:
    snapshot = _start_completed(client)

    invalid_evidence = {**snapshot, "product_evidence": [*snapshot["product_evidence"]]}
    invalid_evidence["product_evidence"][0] = {
        **invalid_evidence["product_evidence"][0],
        "source": "computed",
    }
    with pytest.raises(ValidationError, match="non-fixture Product Evidence"):
        TaskSnapshot.model_validate(invalid_evidence)

    invalid_mode = {**snapshot, "mode": "exact_offer_comparison"}
    with pytest.raises(ValidationError, match="research modes must match"):
        TaskSnapshot.model_validate(invalid_mode)


def test_recent_research_recovers_orphaned_running_snapshot(client: TestClient) -> None:
    thread_id = "recent-interrupted"
    created_at = server._now()
    server._persist_snapshot(
        server.TaskSnapshot(
            snapshot_id=thread_id,
            thread_id=thread_id,
            run_id="a" * 32,
            status="running",
            query="找一款通勤耳机",
            user_id="recent-recovery-user",
            data_mode="sandbox",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    server.records.clear()

    listed = client.get("/api/research", params={"user_id": "recent-recovery-user"})

    assert listed.status_code == 200
    recovered = listed.json()["snapshots"]
    assert len(recovered) == 1
    assert recovered[0]["status"] == "error"
    assert recovered[0]["error_code"] == "task_interrupted"
    assert recovered[0]["events"][-1]["event"] == "error"


def test_read_does_not_interrupt_a_snapshot_owned_by_another_worker(client: TestClient) -> None:
    thread_id = "owned-running-snapshot"
    created_at = server._now()
    server._persist_snapshot(
        server.TaskSnapshot(
            snapshot_id=thread_id,
            thread_id=thread_id,
            run_id="c" * 32,
            status="running",
            query="找一款通勤耳机",
            user_id="owned-snapshot-user",
            data_mode="sandbox",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    owner_handle = server._try_acquire_owner_lock(thread_id)
    assert owner_handle is not None
    server.records.clear()

    try:
        owned = client.get(f"/api/research/{thread_id}")
        assert owned.status_code == 200
        assert owned.json()["status"] == "running"
        assert owned.json()["events"] == []
    finally:
        server._release_lock_handle(owner_handle)

    recovered = client.get(f"/api/research/{thread_id}")
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "error"
    assert recovered.json()["error_code"] == "task_interrupted"


@pytest.mark.asyncio
async def test_concurrent_history_reads_recover_an_orphan_once() -> None:
    thread_id = "concurrent-interrupted"
    created_at = server._now()
    server._persist_snapshot(
        server.TaskSnapshot(
            snapshot_id=thread_id,
            thread_id=thread_id,
            run_id="b" * 32,
            status="running",
            query="找一款通勤耳机",
            user_id="concurrent-recovery-user",
            data_mode="sandbox",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    server.records.clear()
    transport = httpx.ASGITransport(app=server.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            *(
                client.get(
                    "/api/research",
                    params={"user_id": "concurrent-recovery-user"},
                )
                for _ in range(8)
            )
        )

    assert all(response.status_code == 200 for response in responses)
    payloads = [response.json()["snapshots"][0] for response in responses]
    assert all(payload == payloads[0] for payload in payloads)
    assert len(payloads[0]["events"]) == 1


def test_snapshot_persists_applied_preference_and_task_override(client: TestClient) -> None:
    remembered = client.put(
        "/api/preferences/snapshot-user",
        json={"action": "remember", "field": "style_preferences", "values": ["复古"]},
    )
    assert remembered.status_code == 200

    snapshot = _start_completed(client, "这次想要简约风格的耳机")
    assert snapshot["applied_preferences"]["style_preferences"] == ["复古"]
    assert snapshot["task_overrides"]
    assert snapshot["task_overrides"][0]["field"] == "style_preferences"
    assert snapshot["task_overrides"][0]["value"] == "简约"
    assert snapshot["task_overrides"][0]["overridden_values"] == ["复古"]


def test_snapshot_does_not_call_a_current_preference_a_task_override_without_conflict(
    client: TestClient,
) -> None:
    snapshot = _start_completed(client, "这次想要简约风格的耳机")

    assert snapshot["task_overrides"] == []


def test_rerun_creates_lineage_and_never_mutates_parent_snapshot(client: TestClient) -> None:
    parent = _start_completed(client)
    parent_copy = client.get(f"/api/task/{parent['thread_id']}").json()

    rerun = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": parent["user_id"], "idempotency_key": "rerun-parent-1"},
    )
    assert rerun.status_code == 200
    body = rerun.json()
    assert body["thread_id"] != parent["thread_id"]
    assert body["parent_snapshot_id"] == parent["snapshot_id"]
    assert body["lineage"]["relation"] == "rerun"

    child = _wait_for_terminal(client, body["thread_id"])
    assert child["status"] == "completed"
    assert child["lineage"]["parent_thread_id"] == parent["thread_id"]
    assert child["lineage"]["root_snapshot_id"] == parent["snapshot_id"]
    assert client.get(f"/api/task/{parent['thread_id']}").json() == parent_copy

    repeated = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": parent["user_id"], "idempotency_key": "rerun-parent-1"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["thread_id"] == child["thread_id"]
    assert repeated.json()["idempotent"] is True


@pytest.mark.asyncio
async def test_concurrent_keyed_reruns_return_one_child() -> None:
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/task",
            json={
                "query": "找一款轻便降噪耳机",
                "user_id": "concurrent-rerun-user",
                "upload_ids": [],
            },
        )
        assert started.status_code == 202
        parent_id = started.json()["thread_id"]
        for _ in range(100):
            parent = await client.get(f"/api/task/{parent_id}")
            if parent.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("parent task did not complete")

        responses = await asyncio.gather(
            *(
                client.post(
                    f"/api/task/{parent_id}/rerun",
                    json={
                        "user_id": "concurrent-rerun-user",
                        "idempotency_key": "concurrent-rerun-key",
                    },
                )
                for _ in range(8)
            )
        )

    assert all(response.status_code == 200 for response in responses)
    child_ids = {response.json()["thread_id"] for response in responses}
    assert len(child_ids) == 1
    assert sum(response.json()["idempotent"] for response in responses) == len(responses) - 1


@pytest.mark.asyncio
async def test_concurrent_keyed_constraint_relaxations_return_one_child() -> None:
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/task",
            json={
                "query": "找一款轻便降噪耳机，不要皮革",
                "user_id": "concurrent-relaxation-user",
                "upload_ids": [],
            },
        )
        assert started.status_code == 202
        parent_id = started.json()["thread_id"]
        for _ in range(100):
            parent = await client.get(f"/api/task/{parent_id}")
            if parent.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("parent task did not complete")

        constraint_id = parent.json()["resolved_intent"]["hard_constraints"][-1]["id"]
        responses = await asyncio.gather(
            *(
                client.post(
                    f"/api/task/{parent_id}/relaxation",
                    json={
                        "user_id": "concurrent-relaxation-user",
                        "confirmed": True,
                        "constraint_ids": [constraint_id],
                        "idempotency_key": "concurrent-relaxation-key",
                    },
                )
                for _ in range(8)
            )
        )
        child_id = responses[0].json()["thread_id"]
        for _ in range(100):
            child = await client.get(f"/api/task/{child_id}")
            if child.json()["status"] not in {"running", "awaiting_clarification"}:
                break
            await asyncio.sleep(0.01)

    assert all(response.status_code == 200 for response in responses)
    child_ids = {response.json()["thread_id"] for response in responses}
    assert len(child_ids) == 1
    assert sum(response.json()["idempotent"] for response in responses) == len(responses) - 1


def test_failed_rerun_keeps_parent_and_persists_failure_lineage(
    client: TestClient, monkeypatch
) -> None:
    parent = _start_completed(client)
    parent_copy = client.get(f"/api/task/{parent['thread_id']}").json()

    async def fail_rerun(*_args, **_kwargs):
        raise RuntimeError("rerun gateway failed")

    monkeypatch.setattr(server, "run_agent", fail_rerun)
    rerun = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": parent["user_id"], "idempotency_key": "rerun-failure-1"},
    )
    child = _wait_for_terminal(client, rerun.json()["thread_id"])
    assert child["status"] == "error"
    assert child["lineage"]["parent_snapshot_id"] == parent["snapshot_id"]
    assert client.get(f"/api/task/{parent['thread_id']}").json() == parent_copy


def test_cancelled_rerun_keeps_parent_and_persists_child_lineage(
    client: TestClient, monkeypatch
) -> None:
    parent = _start_completed(client)
    parent_copy = client.get(f"/api/task/{parent['thread_id']}").json()
    started = asyncio.Event()

    async def block_rerun(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "run_agent", block_rerun)
    rerun = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": parent["user_id"], "idempotency_key": "rerun-cancel-1"},
    )
    child_id = rerun.json()["thread_id"]
    deadline = time.monotonic() + 2
    while not started.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started.is_set()

    cancelled = client.post(f"/api/task/{child_id}/cancel")
    assert cancelled.status_code == 200
    child = client.get(f"/api/task/{child_id}").json()
    assert child["status"] == "cancelled"
    assert child["lineage"]["parent_snapshot_id"] == parent["snapshot_id"]
    assert client.get(f"/api/task/{parent['thread_id']}").json() == parent_copy


def test_partial_rerun_keeps_parent_and_persists_partial_result(
    client: TestClient, monkeypatch
) -> None:
    parent = _start_completed(client)
    parent_copy = client.get(f"/api/task/{parent['thread_id']}").json()
    source_result = parent_copy["result"]

    async def partial_rerun(request, *_args, **_kwargs):
        payload = {
            **source_result,
            "thread_id": request.thread_id,
            "result_kind": "partial",
            "unavailable_marketplaces": ["ebay"],
        }
        return ShoppingSummaryOutput.model_validate(payload)

    monkeypatch.setattr(server, "run_agent", partial_rerun)
    rerun = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": parent["user_id"], "idempotency_key": "rerun-partial-1"},
    )
    child = _wait_for_terminal(client, rerun.json()["thread_id"])
    assert child["status"] == "completed"
    assert child["result"]["result_kind"] == "partial"
    assert child["lineage"]["parent_snapshot_id"] == parent["snapshot_id"]
    assert client.get(f"/api/task/{parent['thread_id']}").json() == parent_copy


def test_constraint_relaxation_requires_confirmation_and_creates_new_task(
    client: TestClient,
) -> None:
    parent = _start_completed(client, "预算 1200 元，找一款耳机，不要皮革")
    parent_copy = client.get(f"/api/task/{parent['thread_id']}").json()
    constraint_id = parent["resolved_intent"]["hard_constraints"][-1]["id"]

    same_key_rerun = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": parent["user_id"], "idempotency_key": "shared-command-key"},
    )
    _wait_for_terminal(client, same_key_rerun.json()["thread_id"])

    not_confirmed = client.post(
        f"/api/task/{parent['thread_id']}/relaxation",
        json={
            "user_id": parent["user_id"],
            "confirmed": False,
            "constraint_ids": [constraint_id],
        },
    )
    assert not_confirmed.status_code == 409
    assert not_confirmed.json()["detail"]["code"] == "constraint_relaxation_confirmation_required"

    relaxed = client.post(
        f"/api/task/{parent['thread_id']}/relaxation",
        json={
            "user_id": parent["user_id"],
            "confirmed": True,
            "constraint_ids": [constraint_id],
            "idempotency_key": "shared-command-key",
        },
    )
    assert relaxed.status_code == 200
    child = _wait_for_terminal(client, relaxed.json()["thread_id"])
    assert child["status"] == "completed"
    assert child["lineage"]["relation"] == "constraint_relaxation"
    assert child["lineage"]["changed_constraints"][0]["constraint_id"] == constraint_id
    assert child["lineage"]["changed_constraints"][0]["action"] == "removed"
    assert constraint_id not in {
        item["id"] for item in child["resolved_intent"]["hard_constraints"]
    }
    assert client.get(f"/api/task/{parent['thread_id']}").json() == parent_copy


def test_constraint_relaxation_rejects_replacement_with_a_new_id(client: TestClient) -> None:
    parent = _start_completed(client, "预算 1200 元，找一款耳机，不要皮革")
    constraint = parent["resolved_intent"]["hard_constraints"][-1]
    replacement = {**constraint, "id": "different_constraint"}

    response = client.post(
        f"/api/task/{parent['thread_id']}/relaxation",
        json={
            "user_id": parent["user_id"],
            "confirmed": True,
            "changes": [{"constraint_id": constraint["id"], "replacement": replacement}],
        },
    )

    assert response.status_code == 422
    assert client.get(f"/api/task/{parent['thread_id']}").json() == parent


def test_restart_and_recent_history_keep_lineage_and_clarification_one_entry(
    client: TestClient,
) -> None:
    parent = _start_completed(client)
    rerun = client.post(
        f"/api/task/{parent['thread_id']}/rerun",
        json={"user_id": parent["user_id"], "idempotency_key": "restart-lineage-1"},
    )
    child = _wait_for_terminal(client, rerun.json()["thread_id"])

    server.records.clear()
    server.task_locks.clear()
    restarted_parent = client.get(f"/api/task/{parent['thread_id']}").json()
    restarted_child = client.get(f"/api/task/{child['thread_id']}").json()
    assert restarted_parent["events"]
    assert restarted_child["lineage"]["parent_snapshot_id"] == parent["snapshot_id"]
    history = client.get("/api/research/snapshots", params={"user_id": parent["user_id"]}).json()[
        "snapshots"
    ]
    assert {item["thread_id"] for item in history} >= {parent["thread_id"], child["thread_id"]}

    clarification = client.post(
        "/api/task",
        json={"query": "比较耳机", "user_id": "clarification-history-user", "upload_ids": []},
    )
    clarification_id = clarification.json()["thread_id"]
    waiting = client.get(f"/api/task/{clarification_id}").json()
    assert waiting["status"] == "awaiting_clarification"
    one_entry = [
        item
        for item in client.get(
            "/api/research", params={"user_id": "clarification-history-user"}
        ).json()["snapshots"]
        if item["thread_id"] == clarification_id
    ]
    assert len(one_entry) == 1

    answered = client.post(
        f"/api/task/{clarification_id}/clarification",
        json={"response": "比较不同产品"},
    )
    assert answered.status_code == 200
    _wait_for_terminal(client, clarification_id)
    after_answer = [
        item
        for item in client.get(
            "/api/research", params={"user_id": "clarification-history-user"}
        ).json()["snapshots"]
        if item["thread_id"] == clarification_id
    ]
    assert len(after_answer) == 1


@pytest.mark.asyncio
async def test_concurrent_snapshot_reads_are_consistent() -> None:
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post(
            "/api/task",
            json={"query": "找一款轻便降噪耳机", "user_id": "concurrent-user", "upload_ids": []},
        )
        assert started.status_code == 202
        thread_id = started.json()["thread_id"]
        for _ in range(100):
            current = await client.get(f"/api/task/{thread_id}")
            if current.json()["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("task did not complete")

        responses = await asyncio.gather(*(client.get(f"/api/task/{thread_id}") for _ in range(12)))
        payloads = [response.json() for response in responses]
        assert all(response.status_code == 200 for response in responses)
        assert all(payload == payloads[0] for payload in payloads)
