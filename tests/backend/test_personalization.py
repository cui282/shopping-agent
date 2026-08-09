from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import main_agent
from app.api import server
from app.memory.store import InMemoryPreferenceStore, RedisPreferenceStore
from app.recall.orchestrator import RecallAdapters, RecallOrchestrator, recall_readiness
from app.schemas import Candidate, LandedCost, OfferProvenance, RememberedPreference, UserTowerInput
from app.tools.decision_engine import decision_engine
from app.tools.planner import planner


def _candidate(item_id: str) -> Candidate:
    return Candidate(
        item_id=item_id,
        platform="amazon",
        title=item_id,
        price=100,
        currency="USD",
        attributes={"style": "简约" if item_id == "minimal" else "复古"},
        provenance=OfferProvenance(
            kind="marketplace_gateway",
            provider="test-feed",
            upstream_source="test",
        ),
        source="live",
    )


class QueryTower:
    async def encode(self, _query: str) -> list[float]:
        return [1.0, 0.0]


class ItemTower:
    async def encode(self, item: Candidate) -> list[float]:
        return [1.0, 0.0] if item.item_id == "minimal" else [0.0, 1.0]


class ANN:
    def search(self, _vector: list[float], top_k: int = 20) -> tuple[list[float], list[int]]:
        return [0.5, 0.5][:top_k], [0, 1][:top_k]


class UserTower:
    def __init__(self, outcome: list[float] | BaseException | None = None) -> None:
        self.outcome = outcome
        self.inputs: list[UserTowerInput] = []

    async def encode(self, user_input: UserTowerInput) -> list[float]:
        self.inputs.append(user_input)
        if isinstance(self.outcome, BaseException):
            if isinstance(self.outcome, asyncio.TimeoutError):
                await asyncio.sleep(1)
            raise self.outcome
        if self.outcome is not None:
            return self.outcome
        return (
            [1.0, 0.0]
            if "简约" in user_input.remembered_preference.style_preferences
            else [0.0, 1.0]
        )


class SlowUserTower(UserTower):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    async def encode(self, user_input: UserTowerInput) -> list[float]:
        self.inputs.append(user_input)
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return [1.0, 0.0]


def _orchestrator(user_tower: UserTower) -> RecallOrchestrator:
    return RecallOrchestrator(
        adapters=RecallAdapters(
            query_tower=QueryTower(),
            item_tower=ItemTower(),
            ann=ANN(),
            user_tower=user_tower,
        )
    )


def _wait_for_terminal(client: TestClient, thread_id: str, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] != "running":
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not become terminal")


def _configure_personalized_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    monkeypatch.setenv("TOWER_USER_ENDPOINT", "http://tower.test/user")


def test_readiness_discloses_optional_user_tower_without_making_it_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personalized_recall(monkeypatch)
    ready = recall_readiness()

    assert ready.personalization is not None
    assert ready.personalization.configured is True
    assert ready.personalization.state == "configured"
    assert ready.personalization.reason_code == "awaiting_saved_preference"
    assert len(ready.channels) == 4

    monkeypatch.delenv("TOWER_ITEM_ENDPOINT")
    missing_item = recall_readiness()
    assert missing_item.personalization is not None
    assert missing_item.personalization.reason_code == "item_tower_not_configured"

    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    monkeypatch.delenv("TOWER_USER_ENDPOINT")
    unavailable = recall_readiness()
    assert unavailable.personalization is not None
    assert unavailable.personalization.configured is False
    assert unavailable.personalization.reason_code == "dual_tower_architecture"


@pytest.mark.asyncio
async def test_explicit_remembered_preference_changes_user_recall_signal_without_query_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personalized_recall(monkeypatch)
    candidates = [_candidate("minimal"), _candidate("retro")]

    minimal_tower = UserTower()
    minimal_result = await _orchestrator(minimal_tower).recall(
        "这次请找复古耳机，不要学习这段查询",
        candidates,
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["简约"]),
        ),
    )
    retro_tower = UserTower()
    retro_result = await _orchestrator(retro_tower).recall(
        "这次请找简约耳机，不要学习这段查询",
        candidates,
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["复古"]),
        ),
    )

    assert minimal_tower.inputs[0].remembered_preference.style_preferences == ["简约"]
    assert retro_tower.inputs[0].remembered_preference.style_preferences == ["复古"]
    assert [item.item_id for item in minimal_result.candidates] != [
        item.item_id for item in retro_result.candidates
    ]
    assert minimal_result.provenance.personalization is not None
    assert minimal_result.provenance.personalization.state == "ready"
    assert minimal_result.provenance.personalization.input_source == "remembered_preference"
    assert minimal_result.provenance.personalization.preference_values == ["简约"]
    assert minimal_result.provenance.personalization.participated is True
    assert minimal_result.provenance.personalization.signal == "user_tower"


@pytest.mark.asyncio
async def test_personalized_order_keeps_deterministic_eligibility_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personalized_recall(monkeypatch)
    candidates = [_candidate("minimal"), _candidate("retro")]
    plan = await planner("找耳机，不要皮革")

    minimal_result = await _orchestrator(UserTower()).recall(
        "找耳机，不要皮革",
        candidates,
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["简约"]),
        ),
    )
    retro_result = await _orchestrator(UserTower()).recall(
        "找耳机，不要皮革",
        candidates,
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["复古"]),
        ),
    )

    def landed(items: list[Candidate]) -> list[LandedCost]:
        return [
            LandedCost(
                **item.model_dump(mode="python"),
                price_cny=718,
                shipping_cny=85,
                duty_cny=85,
                landed_cny=888,
                eta_days=12,
                duty_tier="标准",
            )
            for item in items
        ]

    minimal_decision = decision_engine(
        plan,
        RememberedPreference(style_preferences=["简约"]),
        landed(minimal_result.candidates),
    )
    retro_decision = decision_engine(
        plan,
        RememberedPreference(style_preferences=["复古"]),
        landed(retro_result.candidates),
    )

    def eligibility(result) -> tuple[set[str], set[str], set[str]]:
        return (
            {item.item_id for item in result.recommendations},
            {item.item_id for item in result.exclusions},
            {item.item_id for item in result.unverified_candidates},
        )

    assert eligibility(minimal_decision) == eligibility(retro_decision)


@pytest.mark.asyncio
async def test_no_saved_preference_skips_user_tower_and_preserves_existing_recall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personalized_recall(monkeypatch)
    user_tower = UserTower()

    result = await _orchestrator(user_tower).recall(
        "查询文本不应成为偏好",
        [_candidate("minimal"), _candidate("retro")],
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(),
        ),
    )

    assert user_tower.inputs == []
    assert result.provenance.personalization is not None
    assert result.provenance.personalization.state == "unavailable"
    assert result.provenance.personalization.reason_code == "no_saved_preference"
    assert result.provenance.personalization.input_source == "none"

    monkeypatch.delenv("TOWER_USER_ENDPOINT")
    unconfigured = await _orchestrator(user_tower).recall(
        "查询文本不应成为偏好",
        [_candidate("minimal"), _candidate("retro")],
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["简约"]),
        ),
    )

    assert user_tower.inputs == []
    assert unconfigured.provenance.personalization is not None
    assert unconfigured.provenance.personalization.reason_code == "dual_tower_architecture"
    assert unconfigured.provenance.personalization.input_source == "remembered_preference"
    assert unconfigured.provenance.personalization.preference_values == ["简约"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "reason_code"),
    [
        (RuntimeError("tower down"), "channel_failed"),
        ([math.nan, 0.0], "invalid_response"),
        ([math.inf, 0.0], "invalid_response"),
        ([0.0, 0.0], "invalid_response"),
        ([], "invalid_response"),
        ([1.0, 2.0, 3.0], "dimension_mismatch"),
    ],
)
async def test_user_tower_failure_timeout_and_invalid_vectors_degrade_transparently(
    monkeypatch: pytest.MonkeyPatch,
    outcome: list[float] | BaseException,
    reason_code: str,
) -> None:
    _configure_personalized_recall(monkeypatch)
    candidates = [_candidate("minimal"), _candidate("retro")]
    baseline = await _orchestrator(UserTower()).recall(
        "找耳机",
        candidates,
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["简约"]),
        ),
    )
    failed = await _orchestrator(UserTower(outcome)).recall(
        "找耳机",
        candidates,
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["简约"]),
        ),
    )

    assert failed.candidates == baseline.candidates
    assert failed.provenance.personalization is not None
    assert failed.provenance.personalization.state == "degraded"
    assert failed.provenance.personalization.reason_code == reason_code
    assert failed.provenance.personalization.participated is False


@pytest.mark.asyncio
async def test_user_tower_timeout_cancels_request_and_preserves_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personalized_recall(monkeypatch)
    monkeypatch.setenv("RECALL_TIMEOUT_SECONDS", "1")
    user_tower = SlowUserTower()
    result = await _orchestrator(user_tower).recall(
        "找耳机",
        [_candidate("minimal"), _candidate("retro")],
        category_insight=None,
        top_k=2,
        user_input=UserTowerInput(
            anonymous_shopper_id="shopper-a",
            remembered_preference=RememberedPreference(style_preferences=["简约"]),
        ),
    )

    assert user_tower.cancelled is True
    assert result.provenance.personalization is not None
    assert result.provenance.personalization.reason_code == "timeout"
    assert result.provenance.personalization.state == "degraded"


@pytest.mark.asyncio
async def test_user_tower_inputs_are_isolated_for_concurrent_shoppers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_personalized_recall(monkeypatch)
    user_tower = UserTower()
    orchestrator = _orchestrator(user_tower)

    await asyncio.gather(
        orchestrator.recall(
            "找耳机",
            [_candidate("minimal"), _candidate("retro")],
            category_insight=None,
            top_k=2,
            user_input=UserTowerInput(
                anonymous_shopper_id="shopper-a",
                remembered_preference=RememberedPreference(style_preferences=["简约"]),
            ),
        ),
        orchestrator.recall(
            "找耳机",
            [_candidate("minimal"), _candidate("retro")],
            category_insight=None,
            top_k=2,
            user_input=UserTowerInput(
                anonymous_shopper_id="shopper-b",
                remembered_preference=RememberedPreference(style_preferences=["复古"]),
            ),
        ),
    )

    observed = {
        item.anonymous_shopper_id: item.remembered_preference.style_preferences
        for item in user_tower.inputs
    }
    assert observed == {"shopper-a": ["简约"], "shopper-b": ["复古"]}


def test_memory_update_and_task_override_keep_user_tower_input_explicit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server.preference_store = InMemoryPreferenceStore()
    observed: list[UserTowerInput] = []
    original = main_agent.recall_orchestrator

    class RecordingOrchestrator:
        async def recall(self, query, candidates, *, category_insight, top_k=None, user_input=None):
            if user_input is not None:
                observed.append(user_input)
            return await original.recall(
                query,
                candidates,
                category_insight=category_insight,
                top_k=top_k,
                user_input=user_input,
            )

    monkeypatch.setattr(main_agent, "recall_orchestrator", RecordingOrchestrator())

    remember = client.post(
        "/api/task",
        json={
            "query": "找耳机；以后请记住我喜欢简约风格",
            "user_id": "tower-memory-user",
            "upload_ids": [],
        },
    )
    _wait_for_terminal(client, remember.json()["thread_id"])
    override = client.post(
        "/api/task",
        json={
            "query": "这次想要复古风格的耳机",
            "user_id": "tower-memory-user",
            "upload_ids": [],
        },
    )
    override_snapshot = _wait_for_terminal(client, override.json()["thread_id"])

    assert observed[0].remembered_preference.style_preferences == []
    assert observed[1].remembered_preference.style_preferences == ["简约"]
    assert all(
        "复古" not in values
        for values in (
            observed[1].remembered_preference.style_preferences,
            observed[1].remembered_preference.soft_preferences,
            observed[1].remembered_preference.avoid,
        )
    )
    assert any(
        decision["value"] == "复古" and decision["source"] == "current_request"
        for decision in override_snapshot["result"]["preference_decisions"]
    )


def test_deleting_task_does_not_delete_remembered_preference(client: TestClient) -> None:
    user_id = "tower-delete-user"
    updated = client.put(
        f"/api/preferences/{user_id}",
        json={"action": "remember", "field": "style_preferences", "values": ["简约"]},
    )
    assert updated.status_code == 200
    started = client.post(
        "/api/task",
        json={"query": "找耳机", "user_id": user_id, "upload_ids": []},
    )
    thread_id = started.json()["thread_id"]
    _wait_for_terminal(client, thread_id)

    deleted = client.request("DELETE", f"/api/task/{thread_id}", json={"user_id": user_id})

    assert deleted.status_code == 200
    assert client.get(f"/api/preferences/{user_id}").json()["preferences"] == {
        "style_preferences": ["简约"]
    }


def test_redis_preference_store_survives_a_new_store_instance() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.values.get(key)

        async def set(self, key: str, value: str, *, ex: int) -> None:
            assert ex > 0
            self.values[key] = value

        async def delete(self, key: str) -> None:
            self.values.pop(key, None)

    redis = FakeRedis()
    first = RedisPreferenceStore.__new__(RedisPreferenceStore)
    first._client = redis
    first._ttl_seconds = 3600
    restarted = RedisPreferenceStore.__new__(RedisPreferenceStore)
    restarted._client = redis
    restarted._ttl_seconds = 3600

    async def exercise() -> dict[str, Any]:
        await first.put("restart-user", {"style_preferences": ["简约"]})
        return await restarted.get("restart-user")

    assert asyncio.run(exercise()) == {"style_preferences": ["简约"]}
