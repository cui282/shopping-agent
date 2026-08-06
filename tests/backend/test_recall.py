from __future__ import annotations

import asyncio
import importlib
import time

import pytest
from fastapi.testclient import TestClient
from typing_extensions import Self

from app.agent import main_agent
from app.recall.ann import FaissANN
from app.recall.orchestrator import (
    FaissIndex,
    RecallAdapters,
    RecallOrchestrator,
    recall_readiness,
)
from app.schemas import (
    AttributeDist,
    Bestseller,
    Candidate,
    CategoryInsightOutput,
    OfferProvenance,
    PriceTier,
    ProviderMetadata,
)
from app.tools.category_insight import category_insight


def _candidate(item_id: str, title: str) -> Candidate:
    return Candidate(
        item_id=item_id,
        platform="amazon",
        title=title,
        price=100,
        currency="USD",
        attributes={"weight_kg": 0.3},
        provenance=OfferProvenance(
            kind="marketplace_gateway",
            provider="test-feed",
            upstream_source="test",
        ),
        source="live",
    )


def _opensearch_insight() -> CategoryInsightOutput:
    return CategoryInsightOutput(
        category="耳机",
        components=["主动降噪"],
        bestsellers=[Bestseller(name="测试耳机", why_popular="测试")],
        attributes=[AttributeDist(name="佩戴方式", distribution={"头戴式": 1.0})],
        price_tiers=[PriceTier(tier="mid", range_cny=(500, 1500), notes="测试")],
        confidence=0.9,
        provider=ProviderMetadata(source="live", provider="opensearch"),
    )


class FakeQueryTower:
    def __init__(self, vector: list[float] | Exception) -> None:
        self.vector = vector
        self.calls: list[str] = []

    async def encode(self, query: str) -> list[float]:
        self.calls.append(query)
        if isinstance(self.vector, Exception):
            raise self.vector
        return self.vector


class FakeItemTower:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def encode(self, item: Candidate) -> list[float]:
        self.calls.append(item.item_id)
        return [1.0, 0.0] if item.item_id == "one" else [0.0, 1.0]


class FakeANN:
    def __init__(self, result: tuple[list[float], list[int]] | Exception) -> None:
        self.result = result
        self.calls: list[list[float]] = []

    def search(self, vector: list[float], top_k: int = 20) -> tuple[list[float], list[int]]:
        self.calls.append(vector)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result[0][:top_k], self.result[1][:top_k]


def _adapters(
    query: FakeQueryTower,
    item: FakeItemTower,
    ann: FakeANN,
) -> RecallAdapters:
    return RecallAdapters(query_tower=query, item_tower=item, ann=ann)


@pytest.mark.asyncio
async def test_configured_hybrid_recall_calls_all_fakes_and_orders_product_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    query = FakeQueryTower([1.0, 0.0])
    item = FakeItemTower()
    ann = FakeANN(([0.95, 0.95], [1, 0]))

    candidates = [_candidate("one", "第一款耳机"), _candidate("two", "第二款耳机")]
    result = await RecallOrchestrator(
        adapters=_adapters(query, item, ann),
    ).recall("找耳机", candidates, category_insight=_opensearch_insight(), top_k=2)

    assert [candidate.item_id for candidate in result.candidates] == ["one", "two"]
    assert result.candidates == candidates
    assert query.calls == ["找耳机"]
    assert item.calls == ["one", "two"]
    assert ann.calls == [[1.0, 0.0]]
    assert result.provenance.mode == "hybrid"
    assert result.provenance.participating_channels == [
        "opensearch",
        "query_tower",
        "item_tower",
        "faiss",
    ]
    assert all(channel.state == "ready" for channel in result.provenance.channels.values())


@pytest.mark.asyncio
async def test_faiss_adapter_resolves_runtime_configured_index_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/runtime-item-index.faiss")
    paths: list[str] = []

    class FakeIndex:
        def __init__(self, path: str) -> None:
            paths.append(path)

        def search(self, _vector: list[float], _top_k: int) -> tuple[list[float], list[int]]:
            return [1.0], [0]

    monkeypatch.setattr("app.recall.orchestrator.FaissANN", FakeIndex)

    result = await FaissIndex().search([1.0, 0.0], top_k=1)

    assert result == ([1.0], [0])
    assert paths == ["/tmp/runtime-item-index.faiss"]


@pytest.mark.asyncio
async def test_empty_ann_recall_protects_candidates_and_uses_stable_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    query = FakeQueryTower([1.0, 0.0])
    item = FakeItemTower()
    ann = FakeANN(([], []))
    candidates = [_candidate("one", "第一款耳机"), _candidate("two", "第二款耳机")]

    result = await RecallOrchestrator(
        adapters=_adapters(query, item, ann),
    ).recall("找耳机", candidates, category_insight=None, top_k=2)

    assert [candidate.item_id for candidate in result.candidates] == ["one", "two"]
    assert result.provenance.mode == "deterministic_fallback"
    assert result.provenance.channels["faiss"].state == "degraded"
    assert result.provenance.channels["faiss"].reason_code == "empty_response"
    assert result.provenance.fallback_reason == "faiss_empty_response"


@pytest.mark.asyncio
async def test_empty_product_evidence_does_not_call_optional_recall_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    query = FakeQueryTower([1.0, 0.0])
    item = FakeItemTower()
    ann = FakeANN(([1.0], [0]))

    result = await RecallOrchestrator(adapters=_adapters(query, item, ann)).recall(
        "找耳机", [], category_insight=None
    )

    assert result.candidates == []
    assert result.provenance.input_candidate_count == 0
    assert result.provenance.selected_candidate_count == 0
    assert query.calls == []
    assert item.calls == []
    assert ann.calls == []
    assert result.provenance.mode == "deterministic_fallback"
    assert all(
        channel.reason_code == "empty_candidate_set"
        for name, channel in result.provenance.channels.items()
        if name != "opensearch"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_channel",
    ["query_tower", "item_tower", "faiss"],
)
async def test_each_ann_channel_failure_is_degraded_without_losing_product_facts(
    monkeypatch: pytest.MonkeyPatch,
    failed_channel: str,
) -> None:
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    query = FakeQueryTower(
        RuntimeError("query failed") if failed_channel == "query_tower" else [1.0, 0.0]
    )
    item = FakeItemTower()
    ann = FakeANN(RuntimeError("ann failed") if failed_channel == "faiss" else ([1.0, 0.5], [0, 1]))
    candidates = [_candidate("one", "第一款耳机"), _candidate("two", "第二款耳机")]
    if failed_channel == "item_tower":
        item.encode = _failing_item_encode  # type: ignore[method-assign]

    result = await RecallOrchestrator(
        adapters=_adapters(query, item, ann),
    ).recall("找耳机", candidates, category_insight=None, top_k=2)

    assert result.candidates == candidates
    assert [candidate.item_id for candidate in result.candidates] == ["one", "two"]
    assert result.provenance.mode == "deterministic_fallback"
    assert result.provenance.channels[failed_channel].state in {"degraded", "unavailable"}
    assert result.provenance.fallback_reason


@pytest.mark.asyncio
async def test_combined_optional_channel_failure_is_stable_and_keeps_product_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    query = FakeQueryTower(RuntimeError("query failed"))
    item = FakeItemTower()
    item.encode = _failing_item_encode  # type: ignore[method-assign]
    ann = FakeANN(RuntimeError("ann failed"))
    candidates = [_candidate("one", "第一款耳机"), _candidate("two", "第二款耳机")]
    orchestrator = RecallOrchestrator(adapters=_adapters(query, item, ann))

    first = await orchestrator.recall(
        "找耳机", candidates, category_insight=_opensearch_insight(), top_k=2
    )
    second = await orchestrator.recall(
        "找耳机", candidates, category_insight=_opensearch_insight(), top_k=2
    )

    assert first.candidates == candidates
    assert first.provenance == second.provenance
    assert first.provenance.mode == "deterministic_fallback"
    assert first.provenance.channels["opensearch"].state == "ready"
    assert all(
        channel.state in {"degraded", "unavailable"}
        for name, channel in first.provenance.channels.items()
        if name != "opensearch"
    )


@pytest.mark.asyncio
async def test_opensearch_category_boundary_issues_hybrid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")
    monkeypatch.setenv("OPENSEARCH_CATEGORY_INDEX", "category-index")
    monkeypatch.setenv("OPENSEARCH_SEARCH_PIPELINE", "hybrid-pipeline")
    requests: list[dict[str, object]] = []
    category_module = importlib.import_module("app.tools.category_insight")

    async def encode_query(_category: str) -> list[float]:
        return [0.25, 0.75]

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "structured": {
                                    "components": ["主动降噪"],
                                    "bestsellers": [{"name": "测试耳机", "why_popular": "测试"}],
                                    "attributes": [
                                        {"name": "佩戴方式", "distribution": {"头戴式": 1.0}}
                                    ],
                                    "price_tiers": [
                                        {"tier": "mid", "range_cny": [500, 1500], "notes": "测试"}
                                    ],
                                    "confidence": 0.9,
                                }
                            }
                        }
                    ]
                }
            }

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, url: str, *, params: dict[str, str], json: dict[str, object]
        ) -> FakeResponse:
            requests.append({"url": url, "params": params, "json": json})
            return FakeResponse()

    monkeypatch.setattr(category_module, "encode_query", encode_query)
    monkeypatch.setattr(category_module.httpx, "AsyncClient", FakeClient)

    result = await category_insight("耳机")

    assert result.provider.source == "live"
    assert result.provider.status == "ok"
    assert len(requests) == 1
    assert requests[0]["url"] == "http://opensearch.test/category-index/_search"
    assert requests[0]["params"] == {"search_pipeline": "hybrid-pipeline"}
    assert requests[0]["json"]["query"]["hybrid"]["queries"][1]["knn"]["embedding"]["vector"] == [
        0.25,
        0.75,
    ]


@pytest.mark.asyncio
async def test_opensearch_failure_is_curated_and_recall_discloses_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")
    category_module = importlib.import_module("app.tools.category_insight")
    monkeypatch.setattr(
        category_module,
        "_opensearch_insight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("search timed out")),
    )

    insight = await category_insight("耳机")

    assert insight.provider.source == "curated"
    assert insight.provider.status == "degraded"
    assert "OpenSearch unavailable" in (insight.provider.fallback_reason or "")
    result = await RecallOrchestrator(
        adapters=_adapters(FakeQueryTower([1.0, 0.0]), FakeItemTower(), FakeANN(([], [])))
    ).recall("找耳机", [_candidate("one", "第一款耳机")], category_insight=insight)
    assert result.provenance.channels["opensearch"].state == "degraded"
    assert result.provenance.channels["opensearch"].reason_code == "request_failed"


async def _failing_item_encode(_item: Candidate) -> list[float]:
    raise RuntimeError("item failed")


@pytest.mark.asyncio
async def test_recall_cancellation_propagates_and_does_not_publish_partial_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")

    async def slow_encode(_query: str) -> list[float]:
        await asyncio.sleep(10)
        return [1.0, 0.0]

    query = FakeQueryTower([1.0, 0.0])
    query.encode = slow_encode  # type: ignore[method-assign]
    orchestrator = RecallOrchestrator(
        adapters=_adapters(query, FakeItemTower(), FakeANN(([1.0], [0]))),
    )

    task = asyncio.create_task(
        orchestrator.recall("找耳机", [_candidate("one", "第一款耳机")], category_insight=None)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_category_channel_failure_is_converted_to_curated_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_category(_category: str):
        raise RuntimeError("category provider failed")

    monkeypatch.setattr(main_agent, "category_insight", fail_category)

    insight = await main_agent._category_insight_with_fallback("耳机")

    assert insight.provider.source == "curated"
    assert insight.provider.status == "degraded"
    assert insight.provider.fallback_reason == "category insight failed: RuntimeError"


def test_readiness_reports_recall_configuration_without_claiming_runtime_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")

    readiness = recall_readiness()

    assert readiness.mode == "hybrid"
    assert set(readiness.channels) == {"opensearch", "query_tower", "item_tower", "faiss"}
    assert all(channel.configured for channel in readiness.channels.values())
    assert all(channel.state == "configured" for channel in readiness.channels.values())


def test_readiness_exposes_partial_recall_configuration_and_required_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")

    readiness = recall_readiness()

    assert readiness.mode == "partial_hybrid"
    assert readiness.channels["opensearch"].state == "configured"
    assert readiness.channels["query_tower"].reason_code == "ann_backend_disabled"
    assert readiness.channels["item_tower"].reason_code == "ann_backend_disabled"
    assert readiness.channels["faiss"].reason_code == "backend_disabled"
    assert any("ANN_BACKEND" in action for action in readiness.required_actions)


def _wait_for_terminal(client: TestClient, thread_id: str, timeout: float = 8) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] != "running":
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not reach a terminal state")


def test_configured_recall_runs_in_real_task_path_and_persists_provenance(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")
    monkeypatch.setenv("ANN_BACKEND", "faiss")
    monkeypatch.setenv("ANN_INDEX_PATH", "/tmp/test-item-index.faiss")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("TOWER_ITEM_ENDPOINT", "http://tower.test/item")
    category_started = asyncio.Event()
    marketplace_started = asyncio.Event()
    tower_query_calls: list[str] = []
    tower_item_calls: list[str] = []

    async def category(_category: str, _depth: str = "quick") -> CategoryInsightOutput:
        category_started.set()
        await marketplace_started.wait()
        return _opensearch_insight()

    original_search = main_agent.item_search

    async def concurrent_search(
        query: str, platform: str, top_k: int = 20, user_id: str | None = None
    ):
        marketplace_started.set()
        return await original_search(query, platform, top_k=top_k, user_id=user_id)

    async def encode_query(query: str) -> list[float]:
        tower_query_calls.append(query)
        return [1.0, 0.0]

    async def encode_item(item: dict[str, object]) -> list[float]:
        tower_item_calls.append(str(item["item_id"]))
        return [1.0, 0.0]

    monkeypatch.setattr(main_agent, "category_insight", category)
    monkeypatch.setattr(main_agent, "item_search", concurrent_search)
    monkeypatch.setattr("app.recall.tower_query.encode_query", encode_query)
    monkeypatch.setattr("app.recall.tower_item.encode_item", encode_item)
    monkeypatch.setattr(
        FaissANN,
        "search",
        lambda _self, _vector, top_k=20: ([1.0, 0.9][:top_k], [1, 0][:top_k]),
    )

    started = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "recall-path-user", "upload_ids": []},
    )
    assert started.status_code == 202
    snapshot = _wait_for_terminal(client, started.json()["thread_id"])

    assert snapshot["status"] == "completed"
    result = snapshot["result"]
    assert result["recall_provenance"]["mode"] == "hybrid"
    assert result["recall_provenance"]["participating_channels"] == [
        "opensearch",
        "query_tower",
        "item_tower",
        "faiss",
    ]
    assert result["recall_provenance"]["selected_candidate_count"] == 2
    evidence_ids = {item["item_id"] for item in result["product_evidence"]}
    assert all(item["item_id"] in evidence_ids for item in result["recommendations"])
    assert tower_query_calls == ["找一款降噪耳机"]
    assert len(tower_item_calls) == len(result["product_evidence"])
    assert snapshot["recall_provenance"] == result["recall_provenance"]
    recall_end = next(
        event
        for event in snapshot["events"]
        if event["event"] == "tool_end" and event["data"]["tool_name"] == "recall"
    )
    assert recall_end["data"]["recall_provenance"] == result["recall_provenance"]
    report = client.get(f"/api/files/{started.json()['thread_id']}/shopping-report.md")
    assert report.status_code == 200
    assert "Recall Provenance" in report.text
