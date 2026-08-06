from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import server
from app.schemas import (
    MonitorEvent,
    ProviderMetadata,
    ShoppingSummaryOutput,
    TaskSnapshot,
)


def _wait_for_terminal(client: TestClient, thread_id: str, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/task/{thread_id}")
        if response.status_code == 404:
            raise AssertionError("task was deleted while waiting")
        snapshot = response.json()
        if snapshot["status"] not in {"running", "awaiting_clarification"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not become terminal")


def _persisted_snapshot(
    thread_id: str,
    user_id: str = "delete-user",
    status: str = "completed",
    result: ShoppingSummaryOutput | None = None,
) -> TaskSnapshot:
    now = server._now()
    return TaskSnapshot(
        thread_id=thread_id,
        status=status,  # type: ignore[arg-type]
        query="找一款耳机",
        user_id=user_id,
        data_mode="sandbox",
        created_at=now,
        updated_at=now,
        result=result,
    )


def _summary(
    thread_id: str,
    *,
    match_status: str = "matched",
    unavailable: list[str] | None = None,
) -> ShoppingSummaryOutput:
    providers = {
        name: ProviderMetadata(
            source="fixture",
            provider=f"{name}-sandbox",
            status="unavailable" if unavailable and name in unavailable else "degraded",
            fallback_reason="test provider unavailable"
            if unavailable and name in unavailable
            else None,
            failure_reason="request_failed" if unavailable and name in unavailable else None,
        )
        for name in (["amazon", *unavailable] if unavailable else ["amazon"])
    }
    return ShoppingSummaryOutput(
        thread_id=thread_id,
        final_answer="完成",
        recommendations=[],
        comparison=[],
        files=[],
        provider_mode="sandbox",
        providers=providers,
        calculation_notice="test result",
        match_status=match_status,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("status", ["completed", "cancelled", "error", "awaiting_clarification"])
def test_delete_removes_every_persisted_task_state(client: TestClient, status: str) -> None:
    thread_id = f"delete-{status}"
    snapshot = _persisted_snapshot(thread_id, status=status)
    server._persist_snapshot(snapshot)
    directory = server.output_root() / thread_id
    (directory / "shopping-report.md").write_text("report", encoding="utf-8")
    (directory / "durable-events.ndjson").write_text("event", encoding="utf-8")
    (directory / "reference-images").mkdir()
    (directory / "reference-images" / "bound.png").write_bytes(b"bound")

    deleted = client.request(
        "DELETE",
        f"/api/task/{thread_id}",
        json={"user_id": snapshot.user_id},
    )

    assert deleted.status_code == 200
    assert not directory.exists()
    assert client.get(f"/api/task/{thread_id}").status_code == 404
    assert client.get(f"/api/research/{thread_id}").status_code == 404
    assert client.get(f"/api/reports/{thread_id}").status_code == 404
    missing_file = client.get(f"/api/files/{thread_id}/shopping-report.md")
    assert missing_file.status_code == 404
    assert missing_file.json()["detail"]["code"] == "file_not_found"


def test_delete_requires_a_shopper_command_body(client: TestClient) -> None:
    thread_id = "delete-body-required"
    snapshot = _persisted_snapshot(thread_id, user_id="body-owner")
    server._persist_snapshot(snapshot)

    response = client.delete(f"/api/task/{thread_id}")

    assert response.status_code == 422
    assert (server.output_root() / thread_id / "task.json").is_file()


def test_untrusted_tombstone_cannot_authorize_task_cleanup(client: TestClient) -> None:
    thread_id = "delete-untrusted-tombstone"
    snapshot = _persisted_snapshot(thread_id, user_id="real-owner")
    server._persist_snapshot(snapshot)
    tombstone_directory = server.output_root() / ".task-tombstones"
    tombstone_directory.mkdir(parents=True)
    (tombstone_directory / f"{thread_id}.json").write_text(
        json.dumps({"thread_id": thread_id}),
        encoding="utf-8",
    )

    response = client.request(
        "DELETE",
        f"/api/task/{thread_id}",
        json={"user_id": "attacker"},
    )

    assert response.status_code == 404
    assert (server.output_root() / thread_id / "task.json").is_file()


def test_delete_removes_partial_and_no_match_snapshots(client: TestClient) -> None:
    for suffix, result in (
        ("partial", _summary("delete-partial", unavailable=["ebay"])),
        ("no-match", _summary("delete-no-match", match_status="no_match")),
    ):
        thread_id = f"delete-{suffix}"
        result = result.model_copy(update={"thread_id": thread_id})
        snapshot = _persisted_snapshot(thread_id, result=result)
        server._persist_snapshot(snapshot)

        response = client.request(
            "DELETE",
            f"/api/task/{thread_id}",
            json={"user_id": snapshot.user_id},
        )

        assert response.status_code == 200
        assert not (server.output_root() / thread_id).exists()


def test_legacy_reference_metadata_remains_readable_after_schema_upgrade(
    client: TestClient,
) -> None:
    thread_id = "legacy-reference-task"
    now = server._now()
    snapshot = _persisted_snapshot(
        thread_id, user_id="legacy-owner", status="completed"
    ).model_copy(
        update={
            "events": [
                MonitorEvent(
                    event_id="evt-" + "a" * 32,
                    thread_id=thread_id,
                    sequence=1,
                    event="session_created",
                    message="购物任务已创建",
                    data={
                        "thread_id": thread_id,
                        "reference_images": [
                            {
                                "upload_id": "b" * 32,
                                "name": "b" * 32 + ".png",
                                "content_type": "application/octet-stream",
                                "size": 10,
                            }
                        ],
                        "data_mode": "sandbox",
                    },
                    timestamp=now,
                )
            ]
        }
    )
    server._persist_snapshot(snapshot)

    assert client.get(f"/api/task/{thread_id}").status_code == 200


def test_reference_binding_copies_task_data_without_consuming_unbound_upload(
    client: TestClient,
) -> None:
    upload = client.post(
        "/api/upload",
        files={"file": ("reference.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
    ).json()
    original = server.upload_root() / upload["name"]

    started = client.post(
        "/api/task",
        json={
            "query": "找耳机",
            "user_id": "reference-owner",
            "upload_ids": [upload["upload_id"]],
        },
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]
    snapshot = _wait_for_terminal(client, thread_id)
    reference = next(event for event in snapshot["events"] if event["event"] == "session_created")[
        "data"
    ]["reference_images"][0]
    bound = server.output_root() / thread_id / "reference-images" / upload["name"]

    assert reference["ownership"] == "task_owned_copy"
    assert reference["bound_at"]
    assert bound.read_bytes() == original.read_bytes()

    deleted = client.request(
        "DELETE",
        f"/api/task/{thread_id}",
        json={"user_id": "reference-owner"},
    )

    assert deleted.status_code == 200
    assert original.is_file()
    assert not bound.exists()


def test_failed_reference_binding_does_not_leave_a_task_directory(
    client: TestClient, monkeypatch
) -> None:
    thread_id = "failed-reference-binding"
    uploads = [
        client.post(
            "/api/upload",
            files={"file": (f"reference-{index}.png", b"\x89PNG\r\n\x1a\npayload", "image/png")},
        ).json()
        for index in range(2)
    ]
    original_copy = server._write_reference_copy
    calls = 0

    def fail_second_copy(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("reference copy failed")
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(server, "_write_reference_copy", fail_second_copy)

    with pytest.raises(OSError, match="reference copy failed"):
        client.post(
            "/api/task",
            json={
                "query": "找耳机",
                "thread_id": thread_id,
                "user_id": "reference-owner",
                "upload_ids": [item["upload_id"] for item in uploads],
            },
        )

    assert not (server.output_root() / thread_id).exists()
    assert all((server.upload_root() / item["name"]).is_file() for item in uploads)


@pytest.mark.asyncio
async def test_delete_marks_tombstone_before_late_event_or_snapshot_write() -> None:
    thread_id = "delete-late-write"
    snapshot = _persisted_snapshot(thread_id, status="running")
    server._persist_snapshot(snapshot)

    async def pending() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(pending())
    server.records[thread_id] = server.TaskRecord(
        run_id=snapshot.run_id,
        snapshot=snapshot,
        task=task,
    )
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.request(
            "DELETE",
            f"/api/task/{thread_id}",
            json={"user_id": snapshot.user_id},
        )

        assert deleted.status_code == 200
        with pytest.raises(server.TaskDeletedError):
            server._record_event(
                thread_id,
                "assistant_call",
                "迟到事件",
                {"step": "late"},
                "evt-" + "1" * 32,
                server._now(),
                snapshot.run_id,
            )
        with pytest.raises(server.TaskDeletedError):
            server._persist_snapshot(snapshot)

    assert not (server.output_root() / thread_id).exists()
    assert server.manager.history(thread_id) == []


@pytest.mark.asyncio
async def test_delete_after_restart_removes_orphan_and_rejects_resurrection() -> None:
    thread_id = "delete-after-restart"
    snapshot = _persisted_snapshot(thread_id, status="running")
    server._persist_snapshot(snapshot)
    owner_handle = server._try_acquire_owner_lock(thread_id)
    assert owner_handle is not None
    server.records.clear()
    server.task_locks.clear()

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        deleted = await client.request(
            "DELETE",
            f"/api/task/{thread_id}",
            json={"user_id": snapshot.user_id},
        )
        assert deleted.status_code == 200
        assert (await client.get(f"/api/task/{thread_id}")).status_code == 404
        assert (
            await client.post(
                "/api/task",
                json={
                    "query": "不能复活",
                    "thread_id": thread_id,
                    "user_id": snapshot.user_id,
                    "upload_ids": [],
                },
            )
        ).status_code == 409
        duplicate = await client.request(
            "DELETE",
            f"/api/task/{thread_id}",
            json={"user_id": snapshot.user_id},
        )
        assert duplicate.status_code == 200
    server._release_lock_handle(owner_handle)


def test_delete_is_idempotent_and_does_not_cross_shopper_boundary(client: TestClient) -> None:
    first = _persisted_snapshot("delete-shopper-a", user_id="shopper-a")
    second = _persisted_snapshot("delete-shopper-b", user_id="shopper-b")
    server._persist_snapshot(first)
    server._persist_snapshot(second)
    remembered = client.put(
        "/api/preferences/shopper-a",
        json={"action": "remember", "field": "style_preferences", "values": ["简约"]},
    )
    assert remembered.status_code == 200

    wrong_owner = client.request(
        "DELETE",
        "/api/task/delete-shopper-a",
        json={"user_id": "shopper-b"},
    )
    assert wrong_owner.status_code == 404
    assert (server.output_root() / "delete-shopper-a").is_dir()

    deleted = client.request(
        "DELETE",
        "/api/task/delete-shopper-a",
        json={"user_id": "shopper-a"},
    )
    duplicate = client.request(
        "DELETE",
        "/api/task/delete-shopper-a",
        json={"user_id": "shopper-a"},
    )
    assert deleted.status_code == duplicate.status_code == 200
    assert (server.output_root() / "delete-shopper-b").is_dir()
    assert client.get("/api/preferences/shopper-a").json()["preferences"] == {
        "style_preferences": ["简约"]
    }


def test_delete_rejects_traversal_without_touching_outside_task_directory(
    client: TestClient,
) -> None:
    sentinel = server.output_root().parent / "deletion-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    response = client.request(
        "DELETE",
        "/api/task/%2E%2E%2Fdeletion-sentinel",
        json={"user_id": "deletion-user"},
    )

    assert response.status_code in {400, 404, 422}
    assert sentinel.read_text(encoding="utf-8") == "keep"
