from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Any
from urllib.parse import unquote_plus

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api import server
from app.schemas import MonitorEvent, ShoppingSummaryOutput, TaskRequest, TaskSnapshot
from app.tools.price_compare import MissingExchangeRatesError
from app.utils.thread_ctx import thread_scope


class FailingWebSocket:
    async def send_json(self, _payload: dict[str, Any]) -> None:
        raise OSError("socket disconnected")


class TrackingWebSocket:
    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


def test_health_and_readiness_separate_liveness_from_runtime(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "shopping-agent"

    readiness = client.get("/api/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["status"] == "degraded"
    assert readiness.json()["task_ready"] is True
    assert readiness.json()["runtime_mode"] == "sandbox"
    assert readiness.json()["providers"] == {
        "amazon": {"configured": False, "state": "missing"},
        "shopee": {"configured": False, "state": "missing"},
        "aliexpress": {"configured": False, "state": "missing"},
        "ebay": {"configured": False, "state": "missing"},
    }


@pytest.mark.parametrize("query", ["a", "商" * 4000, f" \t{'商' * 4000}\n"])
def test_task_accepts_query_length_boundaries(client: TestClient, query: str) -> None:
    response = client.post(
        "/api/task",
        json={"query": query, "user_id": "query-boundary-user", "upload_ids": []},
    )

    assert response.status_code == 202
    assert response.json()["thread_id"]


@pytest.mark.parametrize("query", ["", " \t\n", "商" * 4001])
def test_task_rejects_invalid_query_lengths(client: TestClient, query: str) -> None:
    response = client.post(
        "/api/task",
        json={"query": query, "user_id": "invalid-query-user", "upload_ids": []},
    )

    assert response.status_code == 422


def test_unconfigured_live_runtime_rejects_tasks(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")

    response = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "live-user", "upload_ids": []},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runtime_not_ready"
    assert response.headers["X-Request-ID"]


def test_all_enabled_live_providers_unavailable_ends_with_stable_error(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "http://127.0.0.1:1/search")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "1")

    started = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "provider-user", "upload_ids": []},
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]

    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while True:
            terminal = websocket.receive_json()
            if terminal.get("event") in {"task_result", "error"}:
                break

    assert terminal["event"] == "error"
    assert terminal["data"]["code"] == "providers_unavailable"
    snapshot = client.get(f"/api/task/{thread_id}").json()
    assert snapshot["status"] == "error"
    assert snapshot["error_code"] == "providers_unavailable"
    assert snapshot["result"] is None
    failed_tools = [
        event
        for event in snapshot["events"]
        if event["event"] == "tool_end" and event["data"]["outcome"] == "failure"
    ]
    assert len(failed_tools) == 1
    assert failed_tools[0]["data"]["tool_name"] == "item_search"
    assert failed_tools[0]["data"]["source"] == "live"
    assert failed_tools[0]["data"]["status"] == "unavailable"
    assert snapshot["events"][-1]["event"] == "error"


def test_missing_exchange_rates_ends_with_stable_error(client: TestClient, monkeypatch) -> None:
    async def missing_rates(*_args, **_kwargs):
        raise MissingExchangeRatesError({"HKD"})

    monkeypatch.setattr(server, "run_agent", missing_rates)
    started = client.post(
        "/api/task",
        json={"query": "找一个港币商品", "user_id": "fx-user", "upload_ids": []},
    )
    thread_id = started.json()["thread_id"]

    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while True:
            terminal = websocket.receive_json()
            if terminal.get("event") in {"task_result", "error"}:
                break

    assert terminal["event"] == "error"
    assert terminal["data"]["code"] == "fx_rates_unavailable"
    snapshot = client.get(f"/api/task/{thread_id}").json()
    assert snapshot["status"] == "error"
    assert snapshot["error_code"] == "fx_rates_unavailable"
    assert snapshot["result"] is None


def test_task_lifecycle_and_buffered_websocket_replay(client: TestClient) -> None:
    response = client.post(
        "/api/task",
        json={
            "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
            "user_id": "api-user",
            "upload_ids": [],
        },
    )
    assert response.status_code == 202
    thread_id = response.json()["thread_id"]

    events = []
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        websocket.send_json({"type": "ping"})
        while True:
            message = websocket.receive_json()
            if message.get("type") == "pong":
                continue
            if message.get("type") == "monitor_event":
                events.append(message)
            if message.get("event") in {"task_result", "error"}:
                break

    names = [event["event"] for event in events]
    assert names[0] == "session_created"
    assert names[-1] == "task_result"
    assert names.count("fork") == 4
    assert (
        sum(
            event["event"] == "tool_start" and event["data"].get("tool_name") == "item_search"
            for event in events
        )
        == 4
    )
    assert names.index("tool_start") < names.index("task_result")

    snapshot = client.get(f"/api/task/{thread_id}")
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["status"] == "completed"
    assert payload["result"]["provider_mode"] == "sandbox"
    assert "内置参考汇率表" in payload["result"]["calculation_notice"]
    assert "未标注日期" in payload["result"]["calculation_notice"]
    assert 1 <= len(payload["result"]["recommendations"]) <= 3
    for recommendation in payload["result"]["recommendations"]:
        assert recommendation["landed_cny"] >= recommendation["price_cny"]
        assert recommendation["source"] == "fixture"

    report = client.get(f"/api/files/{thread_id}/shopping-report.md")
    assert report.status_code == 200
    assert "到手价比较" in report.text

    preferences = client.get("/api/preferences/api-user").json()["preferences"]
    assert "不含皮革" in preferences["material_preferences"]
    assert client.delete("/api/preferences/api-user").status_code == 200
    assert client.get("/api/preferences/api-user").json()["preferences"] == {}


def test_persistent_timeline_describes_fork_demands_and_tool_outcomes(
    client: TestClient,
) -> None:
    query = "预算 1200 元找一款轻便降噪耳机"
    started = client.post(
        "/api/task",
        json={"query": query, "user_id": "event-contract-user", "upload_ids": []},
    ).json()
    thread_id = started["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while websocket.receive_json().get("event") != "task_result":
            pass

    events = client.get(f"/api/task/{thread_id}").json()["events"]
    forks = [event for event in events if event["event"] == "fork"]
    assert {event["data"]["platform"] for event in forks} == {
        "amazon",
        "shopee",
        "aliexpress",
        "ebay",
    }
    assert all(
        event["data"]["demand"] == {"platform": event["data"]["platform"], "query": query}
        for event in forks
    )

    tool_ends = [event for event in events if event["event"] == "tool_end"]
    assert tool_ends
    assert all(isinstance(event["data"]["duration_ms"], int) for event in tool_ends)
    assert all(event["data"]["duration_ms"] >= 0 for event in tool_ends)
    assert all(
        event["data"]["outcome"] in {"success", "degraded", "failure"} for event in tool_ends
    )
    assert all(
        event["data"]["source"] in {"live", "curated", "fixture", "computed"} for event in tool_ends
    )
    assert all(event["data"]["status"] in {"ok", "degraded", "unavailable"} for event in tool_ends)
    assert all(event["data"]["provider"] for event in tool_ends)


def test_upload_restricts_media_type_and_size(client: TestClient) -> None:
    good = client.post(
        "/api/upload",
        files={"file": ("reference.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
    )
    assert good.status_code == 200
    assert good.json()["name"].endswith(".png")

    bad = client.post("/api/upload", files={"file": ("payload.txt", b"text", "text/plain")})
    assert bad.status_code == 415
    spoofed = client.post("/api/upload", files={"file": ("spoofed.png", b"not-a-png", "image/png")})
    assert spoofed.status_code == 422


def test_task_validates_and_exposes_upload_references(client: TestClient) -> None:
    upload = client.post(
        "/api/upload",
        files={"file": ("reference.webp", b"RIFF\x04\x00\x00\x00WEBPdata", "image/webp")},
    ).json()
    missing = client.post(
        "/api/task",
        json={"query": "找耳机", "user_id": "upload-user", "upload_ids": ["0" * 32]},
    )
    assert missing.status_code == 422

    started = client.post(
        "/api/task",
        json={"query": "找耳机", "user_id": "upload-user", "upload_ids": [upload["upload_id"]]},
    )
    thread_id = started.json()["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        snapshot_message = websocket.receive_json()
        first_event = websocket.receive_json()
    assert snapshot_message["type"] == "task_snapshot"
    assert first_event["event"] == "session_created"
    assert first_event["data"]["reference_images"][0]["upload_id"] == upload["upload_id"]


def test_unknown_task_and_file_are_404(client: TestClient) -> None:
    assert client.get("/api/task/not-found").status_code == 404
    assert client.get("/api/files/not-found/report.md").status_code == 404


def test_unknown_task_websocket_is_rejected(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as disconnected,
        client.websocket_connect("/ws/not-found"),
    ):
        pass

    assert disconnected.value.code == 1008
    assert "not-found" not in server.manager.active


def test_completed_task_can_be_deleted(client: TestClient) -> None:
    thread_id = "delete-completed-task"
    created_at = server._now()
    server._persist_snapshot(
        TaskSnapshot(
            thread_id=thread_id,
            status="completed",
            query="找一款适合长辈使用的手机",
            user_id="delete-user",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    task_directory = server.output_root() / thread_id
    assert task_directory.is_dir()

    deleted = client.delete(f"/api/task/{thread_id}")

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "thread_id": thread_id}
    assert client.get(f"/api/task/{thread_id}").status_code == 404
    assert not task_directory.exists()


def test_deleting_missing_task_is_idempotent(client: TestClient) -> None:
    deleted = client.delete("/api/task/stale-task-id")

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "thread_id": "stale-task-id"}


def test_active_task_delete_cancels_worker_and_removes_record(
    client: TestClient, monkeypatch
) -> None:
    original = server.run_agent

    async def slow_agent(*args, **kwargs):
        await asyncio.sleep(60)
        return await original(*args, **kwargs)

    monkeypatch.setattr(server, "run_agent", slow_agent)
    started = client.post(
        "/api/task",
        json={"query": "找一款手机", "user_id": "active-delete-user", "upload_ids": []},
    ).json()
    thread_id = started["thread_id"]

    deleted = client.delete(f"/api/task/{thread_id}")

    assert deleted.status_code == 200
    assert thread_id not in server.records
    assert not (server.output_root() / thread_id).exists()
    assert server.manager.history(thread_id) == []


def test_arbitrary_product_query_drives_sandbox_comparison(client: TestClient) -> None:
    query = "找一款天文望远镜，适合城市观星，预算 3000 元"
    started = client.post(
        "/api/task",
        json={
            "query": query,
            "user_id": "arbitrary-query-user",
            "upload_ids": [],
        },
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while websocket.receive_json().get("event") != "task_result":
            pass

    snapshot = client.get(f"/api/task/{thread_id}").json()
    recommendations = snapshot["result"]["recommendations"]

    assert snapshot["status"] == "completed"
    assert snapshot["query"] == query
    assert snapshot["result"]["provider_mode"] == "sandbox"
    assert recommendations
    assert all("天文望远镜" in item["title"] for item in recommendations)
    assert all("天文望远镜" in unquote_plus(item["product_url"]) for item in recommendations)


def test_completed_snapshot_survives_in_memory_record_cleanup(client: TestClient) -> None:
    started = client.post(
        "/api/task",
        json={"query": "预算 800 元找键盘", "user_id": "snapshot-user", "upload_ids": []},
    ).json()
    thread_id = started["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while websocket.receive_json().get("event") != "task_result":
            pass

    server.records.clear()
    restored = client.get(f"/api/task/{thread_id}")
    assert restored.status_code == 200
    assert restored.json()["status"] == "completed"
    assert restored.json()["result"]["thread_id"] == thread_id


def test_completed_task_restores_its_complete_ordered_timeline_after_memory_reset(
    client: TestClient,
) -> None:
    started = client.post(
        "/api/task",
        json={"query": "预算 800 元找键盘", "user_id": "timeline-user", "upload_ids": []},
    ).json()
    thread_id = started["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while websocket.receive_json().get("event") != "task_result":
            pass

    live_snapshot = client.get(f"/api/task/{thread_id}").json()
    live_events = live_snapshot["events"]
    assert len(live_snapshot["run_id"]) == 32
    assert len(live_events) > 18
    assert [event["sequence"] for event in live_events] == list(range(1, len(live_events) + 1))
    assert len({event["event_id"] for event in live_events}) == len(live_events)
    assert all(event["event_id"].startswith("evt-") for event in live_events)
    assert {event["run_id"] for event in live_events} == {live_snapshot["run_id"]}
    assert all(event["timestamp"].endswith("Z") for event in live_events)
    assert live_events[-1]["event"] == "task_result"

    server.records.clear()
    server.task_locks.clear()
    server.manager.active.clear()
    server.manager._events.clear()

    restored = client.get(f"/api/task/{thread_id}").json()
    assert restored["status"] == "completed"
    assert restored["result"]["thread_id"] == thread_id
    assert restored["events"] == live_events


def test_task_creation_does_not_start_when_initial_persistence_fails(
    client: TestClient, monkeypatch
) -> None:
    thread_id = "persist-create-failure"

    def fail_persistence(_snapshot: TaskSnapshot) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(server, "_persist_snapshot", fail_persistence)

    with pytest.raises(OSError, match="disk full"):
        client.post(
            "/api/task",
            json={
                "query": "找一款通勤耳机",
                "thread_id": thread_id,
                "user_id": "persistence-user",
            },
        )

    assert thread_id not in server.records


@pytest.mark.asyncio
async def test_event_is_not_committed_or_broadcast_when_persistence_fails(monkeypatch) -> None:
    thread_id = "persist-event-failure"
    pending = asyncio.create_task(asyncio.Event().wait())
    created_at = server._now()
    snapshot = TaskSnapshot(
        thread_id=thread_id,
        run_id="1" * 32,
        status="running",
        query="找一款通勤耳机",
        user_id="persistence-user",
        created_at=created_at,
        updated_at=created_at,
    )
    record = server.TaskRecord(run_id=snapshot.run_id, snapshot=snapshot, task=pending)
    server.records[thread_id] = record
    sent: list[dict[str, Any]] = []

    def fail_persistence(_snapshot: TaskSnapshot) -> None:
        raise OSError("disk full")

    async def capture(_thread_id: str, payload: dict[str, Any]) -> None:
        sent.append(payload)

    monkeypatch.setattr(server, "_persist_snapshot", fail_persistence)
    monkeypatch.setattr(server.manager, "send_to_thread", capture)

    try:
        with pytest.raises(OSError, match="disk full"):
            await server.monitor.emit(
                thread_id,
                "assistant_call",
                data={"step": "thinking"},
            )
        assert record.snapshot is snapshot
        assert record.snapshot.events == []
        assert sent == []
    finally:
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["write", "session_dir"])
async def test_worker_releases_running_record_when_persistence_remains_unavailable(
    monkeypatch,
    failure_point: str,
) -> None:
    thread_id = f"persist-worker-failure-{failure_point}"
    run_id = "9" * 32
    created_at = server._now()
    snapshot = TaskSnapshot(
        thread_id=thread_id,
        run_id=run_id,
        status="running",
        query="找一款通勤耳机",
        user_id="persistence-user",
        created_at=created_at,
        updated_at=created_at,
    )
    real_persist = server._persist_snapshot
    real_session_dir = server.session_dir
    real_persist(snapshot)
    directory = real_session_dir(thread_id)

    def fail_persistence(_snapshot: TaskSnapshot) -> None:
        raise server.SnapshotPersistenceError("disk full")

    def fail_session_dir(_thread_id: str):
        raise OSError("mount unavailable")

    if failure_point == "write":
        monkeypatch.setattr(server, "_persist_snapshot", fail_persistence)
    else:
        monkeypatch.setattr(server, "session_dir", fail_session_dir)
    request = TaskRequest(
        query=snapshot.query,
        thread_id=thread_id,
        user_id=snapshot.user_id,
    )
    worker = asyncio.create_task(server._execute(request, run_id, directory, []))
    server.records[thread_id] = server.TaskRecord(
        run_id=run_id,
        snapshot=snapshot,
        task=worker,
    )
    websocket = TrackingWebSocket()
    server.manager.active[thread_id] = websocket  # type: ignore[assignment]

    await worker

    assert thread_id not in server.records
    assert websocket.closed == (1011, "timeline persistence failed")

    monkeypatch.setattr(server, "_persist_snapshot", real_persist)
    monkeypatch.setattr(server, "session_dir", real_session_dir)
    restored = server._load_snapshot(thread_id)
    assert restored is not None
    assert restored.status == "error"
    assert restored.error_code == "task_interrupted"
    assert restored.events[-1].event == "error"


@pytest.mark.asyncio
async def test_terminal_snapshot_rejects_late_non_terminal_events() -> None:
    thread_id = "terminal-event-boundary"
    pending = asyncio.create_task(asyncio.Event().wait())
    created_at = server._now()
    snapshot = TaskSnapshot(
        thread_id=thread_id,
        run_id="2" * 32,
        status="cancelled",
        query="找一款通勤耳机",
        user_id="terminal-user",
        created_at=created_at,
        updated_at=created_at,
    )
    server.records[thread_id] = server.TaskRecord(
        run_id=snapshot.run_id,
        snapshot=snapshot,
        task=pending,
    )
    server._persist_snapshot(snapshot)

    try:
        with pytest.raises(RuntimeError, match="terminal task"):
            await server.monitor.emit(
                thread_id,
                "assistant_call",
                data={"step": "observing"},
            )
        assert server.records[thread_id].snapshot == snapshot
        assert server.manager.history(thread_id) == []
    finally:
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending


@pytest.mark.asyncio
async def test_replacement_rejects_events_from_a_superseded_run(tmp_path) -> None:
    thread_id = "superseded-event-boundary"
    pending = asyncio.create_task(asyncio.Event().wait())
    created_at = server._now()
    snapshot = TaskSnapshot(
        thread_id=thread_id,
        run_id="5" * 32,
        status="running",
        query="替换后的研究",
        user_id="replacement-user",
        created_at=created_at,
        updated_at=created_at,
    )
    server.records[thread_id] = server.TaskRecord(
        run_id=snapshot.run_id,
        snapshot=snapshot,
        task=pending,
    )
    server._persist_snapshot(snapshot)

    try:
        with (
            thread_scope(thread_id, tmp_path, "4" * 32),
            pytest.raises(RuntimeError, match="superseded run"),
        ):
            await server.monitor.emit(
                thread_id,
                "assistant_call",
                data={"step": "observing"},
            )
        assert server.records[thread_id].snapshot == snapshot
        assert server.manager.history(thread_id) == []
    finally:
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending


def test_monitor_event_rejects_malformed_typed_payloads() -> None:
    common = {
        "event_id": f"evt-{'1' * 32}",
        "thread_id": "typed-event",
        "sequence": 1,
        "message": "invalid",
        "timestamp": "2026-07-30T12:00:00Z",
    }

    with pytest.raises(ValidationError):
        MonitorEvent(event="fork", data={"platform": "amazon"}, **common)
    with pytest.raises(ValidationError):
        MonitorEvent(
            event="tool_end",
            data={"tool_name": "item_search", "duration_ms": -1},
            **common,
        )
    for event, data in (
        ("session_created", {"thread_id": "typed-event"}),
        ("tool_start", {"tool_name": "item_search"}),
        ("task_result", {}),
        ("task_cancelled", {}),
        ("error", {"thread_id": "typed-event"}),
    ):
        with pytest.raises(ValidationError):
            MonitorEvent(event=event, data=data, **common)


def test_websocket_bootstraps_from_the_durable_snapshot_after_memory_reset(
    client: TestClient,
) -> None:
    started = client.post(
        "/api/task",
        json={"query": "找一款通勤耳机", "user_id": "socket-restore-user"},
    ).json()
    thread_id = started["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while websocket.receive_json().get("event") != "task_result":
            pass

    durable = client.get(f"/api/task/{thread_id}").json()
    server.records.clear()
    server.task_locks.clear()
    server.manager.active.clear()
    server.manager._events.clear()

    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        websocket.send_json({"type": "ping"})
        first = websocket.receive_json()

    assert first["type"] == "task_snapshot"
    assert first["snapshot"] == durable
    assert first["snapshot"]["status"] == "completed"
    assert first["snapshot"]["events"][-1]["event"] == "task_result"


def test_orphaned_running_snapshot_is_marked_interrupted(client: TestClient) -> None:
    thread_id = "interrupted-thread"
    created_at = server._now()
    server._persist_snapshot(
        TaskSnapshot(
            thread_id=thread_id,
            status="running",
            query="找一款通勤耳机",
            user_id="recovery-user",
            created_at=created_at,
            updated_at=created_at,
        )
    )

    restored = client.get(f"/api/task/{thread_id}")

    assert restored.status_code == 200
    assert restored.json()["status"] == "error"
    assert restored.json()["error_code"] == "task_interrupted"


def test_orphaned_running_task_persists_one_interruption_terminal_for_every_reader(
    client: TestClient,
) -> None:
    thread_id = "interrupted-timeline"
    created_at = server._now()
    server._persist_snapshot(
        TaskSnapshot(
            thread_id=thread_id,
            status="running",
            query="找一款通勤耳机",
            user_id="recovery-user",
            created_at=created_at,
            updated_at=created_at,
        )
    )

    first = client.get(f"/api/task/{thread_id}").json()
    second = client.get(f"/api/task/{thread_id}").json()

    assert first == second
    assert first["status"] == "error"
    assert first["error_code"] == "task_interrupted"
    assert len(first["events"]) == 1
    assert first["events"][0]["sequence"] == 1
    assert first["events"][0]["event"] == "error"
    assert first["events"][0]["data"] == {
        "thread_id": thread_id,
        "code": "task_interrupted",
    }

    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        snapshot_message = websocket.receive_json()
    assert snapshot_message["type"] == "task_snapshot"
    assert snapshot_message["snapshot"] == first


def test_file_downloads_are_limited_to_result_file_whitelist(client: TestClient) -> None:
    started = client.post(
        "/api/task",
        json={"query": "预算 800 元找键盘", "user_id": "file-user", "upload_ids": []},
    ).json()
    thread_id = started["thread_id"]
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while websocket.receive_json().get("event") != "task_result":
            pass

    assert client.get(f"/api/files/{thread_id}/shopping-report.md").status_code == 200
    assert client.get(f"/api/files/{thread_id}/task.json").status_code == 404


def test_active_task_can_be_cancelled(client: TestClient, monkeypatch) -> None:
    original = server.run_agent

    async def slow_agent(*args, **kwargs):
        await asyncio.sleep(60)
        return await original(*args, **kwargs)

    monkeypatch.setattr(server, "run_agent", slow_agent)
    started = client.post(
        "/api/task", json={"query": "找一款耳机", "user_id": "cancel-user"}
    ).json()
    thread_id = started["thread_id"]
    cancelled = client.post(f"/api/task/{thread_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/task/{thread_id}").json()["status"] == "cancelled"
    assert [event["event"] for event in server.manager.history(thread_id)][-1] == "task_cancelled"

    durable = client.get(f"/api/task/{thread_id}").json()
    assert [event["event"] for event in durable["events"]].count("task_cancelled") == 1
    server.records.clear()
    server.task_locks.clear()
    server.manager.active.clear()
    server.manager._events.clear()

    restored = client.get(f"/api/task/{thread_id}").json()
    assert restored == durable
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        snapshot_message = websocket.receive_json()
    assert snapshot_message["type"] == "task_snapshot"
    assert snapshot_message["snapshot"] == durable


@pytest.mark.asyncio
async def test_cancel_converges_task_cancelled_before_coroutine_starts() -> None:
    thread_id = "cancel-before-start"

    async def pending() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(pending())
    created_at = server._now()
    snapshot = TaskSnapshot(
        thread_id=thread_id,
        run_id="3" * 32,
        status="running",
        query="找一款耳机",
        user_id="cancel-user",
        created_at=created_at,
        updated_at=created_at,
    )
    server.records[thread_id] = server.TaskRecord(
        run_id=snapshot.run_id,
        snapshot=snapshot,
        task=task,
    )
    server._persist_snapshot(snapshot)

    try:
        response = await server.cancel_task(thread_id)
        assert response["status"] == "cancelled"
        assert server.records[thread_id].snapshot.status == "cancelled"
        assert server.manager.history(thread_id)[-1]["event"] == "task_cancelled"
    finally:
        server.records.pop(thread_id, None)
        server.task_locks.pop(thread_id, None)
        await server.manager.clear(thread_id)


@pytest.mark.asyncio
async def test_completed_snapshot_cannot_be_rolled_back_by_cancel(monkeypatch) -> None:
    terminal_emit_started = asyncio.Event()
    release_terminal_emit = asyncio.Event()
    original_send = server.manager.send_to_thread

    async def completed_agent(request, *_args, **_kwargs):
        return ShoppingSummaryOutput(
            thread_id=request.thread_id,
            final_answer="完成",
            recommendations=[],
            comparison=[],
            files=[],
            provider_mode="sandbox",
            calculation_notice="test result",
        )

    async def delayed_send(thread_id, payload):
        if payload.get("event") == "task_result":
            terminal_emit_started.set()
            await release_terminal_emit.wait()
        return await original_send(thread_id, payload)

    monkeypatch.setattr(server, "run_agent", completed_agent)
    monkeypatch.setattr(server.manager, "send_to_thread", delayed_send)
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = await client.post(
            "/api/task",
            json={"query": "找一款耳机", "user_id": "terminal-race-user"},
        )
        thread_id = started.json()["thread_id"]
        await asyncio.wait_for(terminal_emit_started.wait(), timeout=1)

        cancelled = await client.post(f"/api/task/{thread_id}/cancel")
        assert cancelled.json()["status"] == "completed"
        release_terminal_emit.set()
        await asyncio.wait_for(server.records[thread_id].task, timeout=1)
        snapshot = (await client.get(f"/api/task/{thread_id}")).json()

    assert snapshot["status"] == "completed"
    assert snapshot["result"] is not None


def test_websocket_send_failure_does_not_change_successful_task(
    client: TestClient, monkeypatch
) -> None:
    thread_id = "transport-failure-thread"

    async def delayed_agent(request, *_args, **_kwargs):
        await asyncio.sleep(0.05)
        return ShoppingSummaryOutput(
            thread_id=request.thread_id,
            final_answer="完成",
            recommendations=[],
            comparison=[],
            files=[],
            provider_mode="sandbox",
            calculation_notice="test result",
        )

    monkeypatch.setattr(server, "run_agent", delayed_agent)

    started = client.post(
        "/api/task",
        json={"query": "预算 800 元找键盘", "thread_id": thread_id, "user_id": "ws-user"},
    )
    assert started.status_code == 202
    server.manager.active[thread_id] = FailingWebSocket()  # type: ignore[assignment]

    for _ in range(100):
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] != "running":
            break
        time.sleep(0.01)
    else:
        pytest.fail("task did not reach a terminal state")

    assert snapshot["status"] == "completed"
    assert snapshot["result"] is not None


def test_same_thread_replacement_does_not_publish_a_stale_cancellation(
    client: TestClient, monkeypatch
) -> None:
    old_started = asyncio.Event()

    async def controlled_agent(request, *_args, **_kwargs):
        if request.query == "old request":
            old_started.set()
            await asyncio.Event().wait()
        return ShoppingSummaryOutput(
            thread_id=request.thread_id,
            final_answer=request.query,
            recommendations=[],
            comparison=[],
            files=[],
            provider_mode="sandbox",
            calculation_notice="test result",
        )

    monkeypatch.setattr(server, "run_agent", controlled_agent)
    initial = client.post(
        "/api/task",
        json={
            "query": "old request",
            "thread_id": "replacement-socket",
            "user_id": "replacement-user",
        },
    )
    assert initial.status_code == 202

    received = []
    with client.websocket_connect("/ws/replacement-socket") as websocket:
        while True:
            message = websocket.receive_json()
            received.append(message)
            if message.get("event") == "session_created":
                break
        initial_run_id = received[0]["snapshot"]["run_id"]

        replacement = client.post(
            "/api/task",
            json={
                "query": "replacement request",
                "thread_id": "replacement-socket",
                "user_id": "replacement-user",
            },
        )
        assert replacement.status_code == 202
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()

    event_names = [
        message["event"] for message in received if message.get("type") == "monitor_event"
    ]
    assert "task_cancelled" not in event_names
    assert "task_result" not in event_names

    for _ in range(100):
        persisted = client.get("/api/task/replacement-socket").json()
        if persisted["status"] == "completed":
            break
        time.sleep(0.01)
    else:
        pytest.fail("replacement task did not complete")

    with client.websocket_connect("/ws/replacement-socket") as replacement_socket:
        replacement_snapshot = replacement_socket.receive_json()

    assert replacement_snapshot["type"] == "task_snapshot"
    assert replacement_snapshot["snapshot"] == persisted
    assert persisted["status"] == "completed"
    assert persisted["query"] == "replacement request"
    assert persisted["run_id"] != initial_run_id
    assert {event["run_id"] for event in persisted["events"]} == {persisted["run_id"]}
    assert [event["sequence"] for event in persisted["events"]] == list(
        range(1, len(persisted["events"]) + 1)
    )
    assert [event["event"] for event in persisted["events"]].count("task_result") == 1


@pytest.mark.asyncio
async def test_concurrent_replacements_for_same_thread_are_serialized(monkeypatch) -> None:
    old_started = asyncio.Event()
    release_replacements = asyncio.Event()
    active_replacements = 0
    max_active_replacements = 0

    async def controlled_agent(request, *_args, **_kwargs):
        nonlocal active_replacements, max_active_replacements
        if request.query == "old request":
            old_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                raise

        active_replacements += 1
        max_active_replacements = max(max_active_replacements, active_replacements)
        try:
            await release_replacements.wait()
            return ShoppingSummaryOutput(
                thread_id=request.thread_id,
                final_answer=request.query,
                recommendations=[],
                comparison=[],
                files=[],
                provider_mode="sandbox",
                calculation_notice="test result",
            )
        finally:
            active_replacements -= 1

    monkeypatch.setattr(server, "run_agent", controlled_agent)
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        initial = await client.post(
            "/api/task",
            json={
                "query": "old request",
                "thread_id": "shared-thread",
                "user_id": "race-user",
            },
        )
        assert initial.status_code == 202
        await asyncio.wait_for(old_started.wait(), timeout=1)

        responses = await asyncio.gather(
            client.post(
                "/api/task",
                json={
                    "query": "replacement a",
                    "thread_id": "shared-thread",
                    "user_id": "race-user",
                },
            ),
            client.post(
                "/api/task",
                json={
                    "query": "replacement b",
                    "thread_id": "shared-thread",
                    "user_id": "race-user",
                },
            ),
        )
        assert [response.status_code for response in responses] == [202, 202]
        release_replacements.set()

        for _ in range(100):
            snapshot = (await client.get("/api/task/shared-thread")).json()
            if snapshot["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("replacement task did not complete")

    assert max_active_replacements == 1
    assert (
        sum(event["event"] == "task_result" for event in server.manager.history("shared-thread"))
        == 1
    )
