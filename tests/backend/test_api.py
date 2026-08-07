from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api import server
from app.schemas import (
    Candidate,
    ItemSearchOutput,
    MonitorEvent,
    ProviderMetadata,
    ShoppingSummaryOutput,
    TaskRequest,
    TaskSnapshot,
)
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


def _wait_for_terminal_snapshot(
    client: TestClient, thread_id: str, timeout: float = 5
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] != "running":
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not reach a terminal state within {timeout}s")


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
    assert readiness.json()["data_mode"] == "sandbox"
    assert readiness.json()["preference_backend"] == {
        "requested_backend": "memory",
        "backend": "memory",
        "durability": "local_evaluation",
        "fallback_reason": None,
    }
    assert all(
        capability
        == {
            "configured": False,
            "state": "missing",
            "available": True,
            "source": "fixture",
            "failure_reason": None,
        }
        for capability in readiness.json()["providers"].values()
    )
    components = readiness.json()["components"]
    assert set(components) == {
        "llm",
        "marketplace_gateways",
        "redis",
        "opensearch",
        "faiss",
        "query_tower",
        "item_tower",
        "user_tower",
        "storage",
        "image_analysis",
    }
    assert components["storage"]["ready"] is True
    assert components["storage"]["state"] == "ready"
    assert components["redis"]["state"] == "disabled"
    assert components["image_analysis"] == {
        "configured": False,
        "ready": False,
        "state": "disabled",
        "reason_code": "not_configured",
        "reason": "image analysis is not configured; image upload remains a separate storage capability",
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


def test_unsupported_destination_fails_before_marketplace_search(
    client: TestClient, monkeypatch
) -> None:
    searched: list[str] = []

    async def unexpected_search(query, platform, top_k=20, user_id=None):
        del top_k, user_id
        searched.append(f"{platform}:{query}")
        raise AssertionError("marketplace search must not run for unsupported destination")

    monkeypatch.setattr("app.agent.main_agent.item_search", unexpected_search)
    started = client.post(
        "/api/task",
        json={"query": "找耳机，寄到美国", "user_id": "destination-user", "upload_ids": []},
    )

    assert started.status_code == 202
    snapshot = _wait_for_terminal_snapshot(client, started.json()["thread_id"])

    assert snapshot["status"] == "error"
    assert snapshot["error_code"] == "unsupported_capability"
    assert "中国大陆" in snapshot["error"]
    assert searched == []
    assert not any(
        event["event"] == "tool_start" and event["data"].get("tool_name") == "item_search"
        for event in snapshot["events"]
    )


def test_unconfigured_live_runtime_rejects_tasks(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")

    readiness = client.get("/api/readiness")
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["task_ready"] is False
    assert all(
        component["state"] == "unavailable" and component["reason_code"] == "not_configured"
        for component in body["components"]["marketplace_gateways"].values()
    )

    response = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "live-user", "upload_ids": []},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runtime_not_ready"
    assert response.headers["X-Request-ID"]


def test_readiness_marks_partial_and_configured_live_gateways_without_claiming_health(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("AMAZON_DATA_CHANNEL_ENDPOINT", "https://gateway.example/amazon")

    partial = client.get("/api/readiness").json()
    assert partial["components"]["marketplace_gateways"]["amazon"] == {
        "configured": False,
        "ready": False,
        "state": "degraded",
        "reason_code": "partial_configuration",
        "reason": "data-provider channel endpoint and credential must both be configured",
    }

    monkeypatch.setenv("AMAZON_DATA_CHANNEL_CREDENTIAL", "test-credential")
    configured = client.get("/api/readiness").json()
    assert configured["components"]["marketplace_gateways"]["amazon"] == {
        "configured": True,
        "ready": False,
        "state": "configured",
        "reason_code": "configured_not_probed",
        "reason": "live data-provider channel endpoint and credential are configured; readiness does not call the provider",
    }


def test_production_sandbox_readiness_and_task_fail_closed(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SANDBOX_MODE", "true")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "false")
    monkeypatch.setenv("DEVELOPER_DIAGNOSTIC_MODE", "false")

    readiness = client.get("/api/readiness")
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["status"] == "not_ready"
    assert body["task_ready"] is False
    assert body["data_mode"] == "sandbox"
    assert all(
        capability["available"] is False
        and capability["source"] == "fixture"
        and capability["failure_reason"] == "sandbox_forbidden"
        for capability in body["providers"].values()
    )
    assert all(
        component["state"] == "disabled" and component["reason_code"] == "sandbox_forbidden"
        for component in body["components"]["marketplace_gateways"].values()
    )

    response = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "production-sandbox-user", "upload_ids": []},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runtime_not_ready"


def test_readiness_fails_closed_when_runtime_storage_is_not_a_directory(
    client: TestClient, monkeypatch, tmp_path: Path
) -> None:
    output_file = tmp_path / "output-file"
    output_file.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("OUTPUT_ROOT", str(output_file))

    readiness = client.get("/api/readiness")

    assert readiness.status_code == 200
    body = readiness.json()
    assert body["task_ready"] is False
    assert body["status"] == "not_ready"
    assert body["components"]["storage"]["reason_code"] == "storage_unavailable"
    assert any("OUTPUT_ROOT" in action for action in body["required_actions"])


def test_production_fixture_fallback_readiness_and_task_fail_closed(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setenv("DEVELOPER_DIAGNOSTIC_MODE", "true")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://gateway.example/amazon")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")

    readiness = client.get("/api/readiness")
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["status"] == "not_ready"
    assert body["task_ready"] is False
    assert body["data_mode"] == "live"
    assert "Disable ALLOW_FIXTURE_FALLBACK in production" in body["required_actions"]
    assert "Disable DEVELOPER_DIAGNOSTIC_MODE in production" in body["required_actions"]

    response = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "production-fallback-user", "upload_ids": []},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runtime_not_ready"


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

    snapshot = _wait_for_terminal_snapshot(client, thread_id)
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
    assert failed_tools[0]["data"]["failure_reason"] == "request_failed"
    assert snapshot["events"][-1]["event"] == "error"


def test_live_partial_result_keeps_successful_evidence_and_failed_provider_reason(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    for platform in ("amazon", "ebay"):
        monkeypatch.setenv(
            f"{platform.upper()}_API_ENDPOINT", f"https://gateway.example/{platform}"
        )
        monkeypatch.setenv(f"{platform.upper()}_API_KEY", "test-key")

    async def partial_search(query, platform, top_k=20, user_id=None):
        del query, top_k, user_id
        if platform == "amazon":
            candidate = Candidate(
                item_id="amazon-success",
                platform="amazon",
                title="Live Amazon headphones",
                price=100,
                currency="USD",
                source="live",
            )
            return ItemSearchOutput(
                platform="amazon",
                candidates=[candidate],
                total_recall=1,
                truncated=False,
                provider=ProviderMetadata(source="live", provider="amazon-feed"),
            )
        raise RuntimeError("provider request failed")

    monkeypatch.setattr("app.agent.main_agent.item_search", partial_search)
    started = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "partial-user", "upload_ids": []},
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]

    snapshot = _wait_for_terminal_snapshot(client, thread_id)
    assert snapshot["status"] == "completed"
    result = snapshot["result"]
    assert result["data_mode"] == "live"
    assert result["provider_mode"] == "live"
    assert result["result_kind"] == "partial"
    assert result["unavailable_marketplaces"] == ["aliexpress", "ebay", "shopee"]
    assert result["providers"]["amazon"]["status"] == "ok"
    assert result["providers"]["ebay"]["failure_reason"] == "request_failed"
    assert result["providers"]["shopee"]["failure_reason"] == "not_configured"
    assert result["recommendations"]


def test_multiple_live_provider_failures_end_with_one_stable_error(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    for platform in ("amazon", "ebay"):
        monkeypatch.setenv(
            f"{platform.upper()}_API_ENDPOINT", f"https://gateway.example/{platform}"
        )
        monkeypatch.setenv(f"{platform.upper()}_API_KEY", "test-key")

    async def unavailable_search(query, platform, top_k=20, user_id=None):
        del query, top_k, user_id
        return ItemSearchOutput(
            platform=platform,
            candidates=[],
            total_recall=0,
            truncated=False,
            provider=ProviderMetadata(
                source="live",
                provider=f"{platform}-feed",
                status="unavailable",
                fallback_reason="provider request failed: TimeoutException",
                failure_reason="request_failed",
            ),
        )

    monkeypatch.setattr("app.agent.main_agent.item_search", unavailable_search)
    started = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "all-failed-user", "upload_ids": []},
    )
    thread_id = started.json()["thread_id"]

    snapshot = _wait_for_terminal_snapshot(client, thread_id)
    assert snapshot["status"] == "error"
    assert snapshot["error_code"] == "providers_unavailable"
    failed_tools = [
        event
        for event in snapshot["events"]
        if event["event"] == "tool_end" and event["data"]["outcome"] == "failure"
    ]
    assert {event["data"]["provider"] for event in failed_tools} == {
        "amazon-feed",
        "ebay-feed",
    }
    assert all(event["data"]["failure_reason"] == "request_failed" for event in failed_tools)


def test_mixed_source_requires_diagnostic_configuration_and_stays_out_of_normal_request_api(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setenv("DEVELOPER_DIAGNOSTIC_MODE", "true")
    for platform in ("amazon", "ebay"):
        monkeypatch.setenv(
            f"{platform.upper()}_API_ENDPOINT", f"https://gateway.example/{platform}"
        )
        monkeypatch.setenv(f"{platform.upper()}_API_KEY", "test-key")

    normal_request = client.post(
        "/api/task",
        json={
            "query": "找一款降噪耳机",
            "user_id": "mixed-request-user",
            "upload_ids": [],
            "data_mode": "mixed",
        },
    )
    assert normal_request.status_code == 422
    readiness = client.get("/api/readiness").json()
    assert readiness["data_mode"] == "mixed"
    assert readiness["developer_diagnostic_mode"] is True

    async def diagnostic_search(query, platform, top_k=20, user_id=None):
        del query, top_k, user_id
        candidate = Candidate(
            item_id=f"{platform}-diagnostic",
            platform=platform,
            title=f"{platform} diagnostic item",
            price=100,
            currency="USD",
            source="fixture" if platform == "ebay" else "live",
        )
        return ItemSearchOutput(
            platform=platform,
            candidates=[candidate],
            total_recall=1,
            truncated=False,
            provider=ProviderMetadata(
                source="fixture" if platform == "ebay" else "live",
                provider=f"{platform}-diagnostic",
                status="degraded" if platform == "ebay" else "ok",
                fallback_reason="provider request failed: TimeoutException"
                if platform == "ebay"
                else None,
                failure_reason="request_failed" if platform == "ebay" else None,
            ),
        )

    monkeypatch.setattr("app.agent.main_agent.item_search", diagnostic_search)
    started = client.post(
        "/api/task",
        json={"query": "找一款降噪耳机", "user_id": "diagnostic-user", "upload_ids": []},
    )
    thread_id = started.json()["thread_id"]
    snapshot = _wait_for_terminal_snapshot(client, thread_id)
    assert snapshot["status"] == "completed"
    assert snapshot["result"]["data_mode"] == "mixed"
    assert snapshot["result"]["provider_mode"] == "mixed"
    assert snapshot["result"]["result_kind"] == "partial"
    assert snapshot["result"]["unavailable_marketplaces"] == ["aliexpress", "ebay", "shopee"]
    assert all(event["data"].get("data_mode") == "mixed" for event in snapshot["events"])


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
    assert "effective date：2026-01-01" in payload["result"]["calculation_notice"]
    assert payload["result"]["exchange_rate"] == {
        "base_currency": "CNY",
        "source": "reference-table",
        "effective_date": "2026-01-01",
        "calculation_basis": "original_amount * rate_to_cny",
    }
    assert payload["result"]["calculation_exclusions"] == []
    assert payload["result"]["ranking_profile"] == {
        "priority_order": [
            "landed_cost",
            "preference_match",
            "evidence_quality",
            "delivery_time",
        ],
        "explicit": False,
    }
    assert 1 <= len(payload["result"]["recommendations"]) <= 3
    for recommendation in payload["result"]["recommendations"]:
        assert recommendation["landed_cny"] >= recommendation["price_cny"]
        assert recommendation["shipping_estimate"]["estimated"] is True
        assert recommendation["duty_estimate"]["estimated"] is True
        assert recommendation["delivery_estimate"]["estimated"] is True
        assert (
            recommendation["score_breakdown"]["priority_order"]
            == payload["result"]["ranking_profile"]["priority_order"]
        )
        assert recommendation["source"] == "fixture"
        assert recommendation["marketplace"] == recommendation["platform"]
        assert recommendation["offer_id"] is None
        assert recommendation["identity"] == {
            "gtin": None,
            "mpn": None,
            "brand": None,
            "model": None,
        }
        assert isinstance(recommendation["variant_attributes"], dict)
        assert recommendation["availability"] is None
        assert recommendation["retrieved_at"] is None
        assert recommendation["provenance"]["kind"] == "sandbox_fixture"
        assert recommendation["link_kind"] == "marketplace_search"
        assert recommendation["product_url"].startswith(("http://", "https://"))
    assert events[-1]["data"]["recommendations"] == payload["result"]["recommendations"]

    report = client.get(f"/api/files/{thread_id}/shopping-report.md")
    assert report.status_code == 200
    assert "到手价比较" in report.text
    json_report = client.get(f"/api/files/{thread_id}/shopping-report.json")
    assert json_report.status_code == 200
    assert json_report.json()["recommendations"] == payload["result"]["recommendations"]

    preferences = client.get("/api/preferences/api-user").json()["preferences"]
    assert preferences == {}
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

    deleted = client.request(
        "DELETE",
        f"/api/task/{thread_id}",
        json={"user_id": "delete-user"},
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "thread_id": thread_id}
    assert client.get(f"/api/task/{thread_id}").status_code == 404
    assert not task_directory.exists()


def test_deleting_missing_task_is_idempotent(client: TestClient) -> None:
    deleted = client.request(
        "DELETE",
        "/api/task/stale-task-id",
        json={"user_id": "stale-user"},
    )

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

    deleted = client.request(
        "DELETE",
        f"/api/task/{thread_id}",
        json={"user_id": "active-delete-user"},
    )

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


def test_usable_marketplace_data_returns_successful_no_match_with_decision_evidence(
    client: TestClient,
) -> None:
    started = client.post(
        "/api/task",
        json={
            "query": "预算 1 元，找一款耳机，不要塑料的",
            "user_id": "no-match-user",
            "upload_ids": [],
        },
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]

    snapshot = _wait_for_terminal_snapshot(client, thread_id)

    assert snapshot["status"] == "completed"
    result = snapshot["result"]
    assert result["result_kind"] == "sandbox"
    assert result["match_status"] == "no_match"
    assert result["recommendations"] == []
    assert result["exclusions"]
    assert result["exclusions"][0]["violated_count"] >= 1
    assert result["working_assumptions"]
    assert {item["field"] for item in result["working_assumptions"]} == {"color", "style"}
    assert result["relaxation_suggestions"]
    assert all(item["requires_confirmation"] for item in result["relaxation_suggestions"])
    assert "没有满足全部硬性条件" in result["final_answer"]
    assert snapshot["events"][-1]["event"] == "task_result"

    report = client.get(f"/api/files/{thread_id}/shopping-report.md")
    assert report.status_code == 200
    assert "## 排除项" in report.text
    assert "## 工作假设" in report.text


def test_llm_advisory_cannot_create_evidence_or_change_eligibility(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fabricated_advisory(*_args: Any, **_kwargs: Any) -> str:
        return "我确认规格 X1 已由 Product Evidence 验证，应当推荐。"

    monkeypatch.setattr("app.agent.main_agent.active_agent_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.requested_mode", lambda: "llm")
    monkeypatch.setattr("app.agent.main_agent.allow_rules_fallback", lambda: True)
    monkeypatch.setattr("app.agent.main_agent._run_react_advisory", fabricated_advisory)

    started = client.post(
        "/api/task",
        json={
            "query": "找一款耳机，规格为X1",
            "user_id": "advisory-boundary-user",
            "upload_ids": [],
        },
    )
    assert started.status_code == 202
    snapshot = _wait_for_terminal_snapshot(client, started.json()["thread_id"])

    result = snapshot["result"]
    assert snapshot["status"] == "completed"
    assert result["recommendations"] == []
    assert result["unverified_candidates"]
    assert result["unverified_candidates"][0]["constraint_evaluations"][0]["status"] == "unknown"
    assert result["unverified_candidates"][0]["constraint_evaluations"][0]["evidence"] == []
    assert all(item["source"] == "fixture" for item in result["unverified_candidates"])
    advisory_events = [event for event in snapshot["events"] if event["event"] == "assistant_call"]
    assert any("Product Evidence" in event["data"].get("preview", "") for event in advisory_events)


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
        ("assistant_call", {}),
        ("tool_start", {"tool_name": "item_search"}),
        ("task_result", {}),
        ("task_cancelled", {}),
        ("error", {"thread_id": "typed-event"}),
    ):
        with pytest.raises(ValidationError):
            MonitorEvent(event=event, data=data, **common)


def test_result_contract_rejects_fixture_evidence_marked_as_live() -> None:
    with pytest.raises(ValidationError, match="live result cannot contain fixture evidence"):
        ShoppingSummaryOutput(
            thread_id="typed-result",
            final_answer="invalid",
            recommendations=[],
            comparison=[],
            files=[],
            provider_mode="live",
            providers={"amazon": ProviderMetadata(source="fixture", provider="fixture")},
            calculation_notice="test",
        )


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


def test_legacy_sandbox_snapshot_is_normalized_without_rewriting_storage(
    client: TestClient,
) -> None:
    thread_id = "legacy-sandbox-snapshot"
    created_at = server._now()
    snapshot = TaskSnapshot(
        thread_id=thread_id,
        status="completed",
        query="找一款通勤耳机",
        user_id="legacy-user",
        data_mode="sandbox",
        created_at=created_at,
        updated_at=created_at,
        events=[
            MonitorEvent(
                event_id="evt-" + "1" * 32,
                thread_id=thread_id,
                sequence=1,
                event="assistant_call",
                message="正在分析需求",
                data={"step": "thinking", "source": "live"},
                timestamp=created_at,
            ),
            MonitorEvent(
                event_id="evt-" + "2" * 32,
                thread_id=thread_id,
                sequence=2,
                event="tool_end",
                message="商品检索已完成",
                data={
                    "tool_name": "item_search",
                    "duration_ms": 1,
                    "outcome": "degraded",
                    "source": "fixture",
                    "provider": "amazon-sandbox",
                    "status": "degraded",
                    "fallback_reason": "已显式启用沙盒模式",
                },
                timestamp=created_at,
            ),
        ],
        result=ShoppingSummaryOutput(
            thread_id=thread_id,
            final_answer="完成",
            recommendations=[],
            comparison=[],
            files=[],
            provider_mode="sandbox",
            calculation_notice="test result",
        ),
    )
    path = server.session_dir(thread_id) / "task.json"
    payload = snapshot.model_dump(mode="json")
    payload.pop("data_mode")
    for event in payload["events"]:
        event["data"].pop("data_mode")
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    restored = client.get(f"/api/task/{thread_id}")

    assert restored.status_code == 200
    assert restored.json()["data_mode"] == "sandbox"
    assert all(event["data"]["data_mode"] == "sandbox" for event in restored.json()["events"])
    assert restored.json()["result"]["data_mode"] == "sandbox"
    assert path.read_bytes() == before


def test_orphaned_running_snapshot_is_marked_interrupted(client: TestClient) -> None:
    thread_id = "interrupted-thread"
    created_at = server._now()
    server._persist_snapshot(
        TaskSnapshot(
            thread_id=thread_id,
            status="running",
            query="找一款通勤耳机",
            user_id="recovery-user",
            data_mode="sandbox",
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
            data_mode="sandbox",
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
        "data_mode": "sandbox",
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
