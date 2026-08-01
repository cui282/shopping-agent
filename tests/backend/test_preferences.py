from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import server
from app.memory import store as store_module
from app.memory.commands import parse_memory_commands
from app.memory.store import InMemoryPreferenceStore
from app.schemas import (
    LandedCost,
    OfferProvenance,
    RememberedPreference,
)
from app.tools.decision_engine import decision_engine
from app.tools.planner import planner


def _landed_offer(
    item_id: str,
    *,
    title: str = "通勤耳机",
    landed_cny: float = 888,
    attributes: dict[str, object] | None = None,
) -> LandedCost:
    return LandedCost(
        item_id=item_id,
        platform="amazon",
        title=title,
        price=100,
        currency="USD",
        price_cny=718,
        shipping_cny=85,
        duty_cny=85,
        landed_cny=landed_cny,
        eta_days=12,
        duty_tier="标准",
        attributes=attributes or {},
        source="live",
        provenance=OfferProvenance(
            kind="marketplace_gateway",
            provider="test-gateway",
            upstream_source="test-catalog",
        ),
    )


def _wait_for_terminal(client: TestClient, thread_id: str, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] != "running":
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not become terminal")


def test_plain_request_is_not_a_memory_command() -> None:
    assert parse_memory_commands("预算 1200 元，找耳机，不要皮革") == []


def test_memory_command_parser_requires_explicit_future_scope() -> None:
    commands = parse_memory_commands("找耳机；以后请记住我喜欢金属材质，今后偏好简约风格")

    assert [(command.action, command.field, command.values) for command in commands] == [
        ("remember", "material_preferences", ["金属"]),
        ("remember", "style_preferences", ["简约"]),
    ]

    forget = parse_memory_commands("找耳机；忘记我喜欢金属材质")
    assert [(command.action, command.field, command.values) for command in forget] == [
        ("forget", "material_preferences", ["金属"])
    ]


@pytest.mark.asyncio
async def test_current_preference_overrides_remembered_for_one_task() -> None:
    plan = await planner("这次想要简约风格的耳机")

    result = decision_engine(
        plan,
        RememberedPreference(style_preferences=["复古"]),
        [_landed_offer("candidate", title="简约通勤耳机", attributes={"style": "简约"})],
    )

    assert [item.item_id for item in result.recommendations] == ["candidate"]
    assert {(item.value, item.status, item.source) for item in result.preference_decisions} == {
        ("简约", "applied", "current_request"),
        ("复古", "overridden", "remembered_preference"),
    }


def test_explicit_memory_api_updates_and_forgets_without_implicit_learning(
    client: TestClient,
) -> None:
    user_id = "explicit-memory-user"

    initial = client.get(f"/api/preferences/{user_id}")
    assert initial.status_code == 200
    assert initial.json()["preferences"] == {}
    assert initial.json()["backend"]["durability"] == "local_evaluation"

    ordinary = client.post(
        "/api/task",
        json={"query": "找一款轻便降噪耳机，不要皮革", "user_id": user_id, "upload_ids": []},
    )
    assert ordinary.status_code == 202
    _wait_for_terminal(client, ordinary.json()["thread_id"])
    assert client.get(f"/api/preferences/{user_id}").json()["preferences"] == {}

    updated = client.put(
        f"/api/preferences/{user_id}",
        json={
            "action": "remember",
            "field": "style_preferences",
            "values": ["简约"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["preferences"]["style_preferences"] == ["简约"]

    removed = client.put(
        f"/api/preferences/{user_id}",
        json={
            "action": "forget",
            "field": "style_preferences",
            "values": ["简约"],
        },
    )
    assert removed.status_code == 200
    assert removed.json()["preferences"] == {}


def test_explicit_query_update_is_persisted_but_task_override_is_not(client: TestClient) -> None:
    user_id = "command-task-user"
    server.preference_store = InMemoryPreferenceStore()

    command_task = client.post(
        "/api/task",
        json={
            "query": "找耳机；以后请记住我喜欢简约风格",
            "user_id": user_id,
            "upload_ids": [],
        },
    )
    command_snapshot = _wait_for_terminal(client, command_task.json()["thread_id"])
    assert command_snapshot["status"] == "completed"
    assert client.get(f"/api/preferences/{user_id}").json()["preferences"] == {
        "style_preferences": ["简约"]
    }

    override_task = client.post(
        "/api/task",
        json={
            "query": "这次想要复古风格的耳机",
            "user_id": user_id,
            "upload_ids": [],
        },
    )
    override_snapshot = _wait_for_terminal(client, override_task.json()["thread_id"])
    assert override_snapshot["status"] == "completed"
    decisions = override_snapshot["result"]["preference_decisions"]
    assert any(
        item["value"] == "简约"
        and item["status"] == "overridden"
        and item["source"] == "remembered_preference"
        for item in decisions
    )
    assert client.get(f"/api/preferences/{user_id}").json()["preferences"] == {
        "style_preferences": ["简约"]
    }


def test_failed_and_cancelled_tasks_do_not_write_memory(client: TestClient, monkeypatch) -> None:
    user_id = "terminal-memory-user"

    async def fail(*_args, **_kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(server, "run_agent", fail)
    failed = client.post(
        "/api/task",
        json={"query": "找耳机", "user_id": user_id, "upload_ids": []},
    )
    failed_snapshot = _wait_for_terminal(client, failed.json()["thread_id"])
    assert failed_snapshot["status"] == "error"
    assert client.get(f"/api/preferences/{user_id}").json()["preferences"] == {}

    async def block(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "run_agent", block)
    cancelled = client.post(
        "/api/task",
        json={"query": "找耳机", "user_id": user_id, "upload_ids": []},
    )
    cancel_response = client.post(f"/api/task/{cancelled.json()['thread_id']}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert client.get(f"/api/preferences/{user_id}").json()["preferences"] == {}


def test_redis_configuration_falls_back_with_explicit_local_status(monkeypatch) -> None:
    monkeypatch.setenv("STORE_BACKEND", "redis")
    monkeypatch.setenv("APP_ENV", "development")

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("redis package or connection unavailable")

    monkeypatch.setattr(store_module, "RedisPreferenceStore", unavailable)
    fallback = store_module.build_preference_store()

    assert fallback.backend_status.model_dump(mode="json") == {
        "requested_backend": "redis",
        "backend": "memory",
        "durability": "local_evaluation",
        "fallback_reason": "Redis backend unavailable: RuntimeError",
    }


def test_production_redis_configuration_fails_closed_when_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("STORE_BACKEND", "redis")
    monkeypatch.setenv("APP_ENV", "production")

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(store_module, "RedisPreferenceStore", unavailable)
    with pytest.raises(store_module.PreferenceStoreError, match="Redis backend unavailable"):
        store_module.build_preference_store()
