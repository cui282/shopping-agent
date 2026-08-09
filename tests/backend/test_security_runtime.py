from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.dispatch_tool import dispatch_tool
from app.api import server
from app.observability import LangFuseObserver
from app.provider_resilience import (
    ProviderCircuitOpenError,
    get_provider_resilience,
)
from app.utils.thread_ctx import thread_scope


def _identity(user_id: str, tenant_id: str = "tenant-a") -> dict[str, str]:
    return {"X-Auth-User": user_id, "X-Auth-Tenant": tenant_id}


def test_authenticated_task_and_file_scope_is_tenant_bound(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    owner = _identity("owner", "tenant-a")
    started = client.post(
        "/api/task",
        headers=owner,
        json={"query": "找一款耳机", "user_id": "owner", "upload_ids": []},
    )
    assert started.status_code == 202
    thread_id = started.json()["thread_id"]

    assert client.get(f"/api/task/{thread_id}").status_code == 401
    assert (
        client.get(f"/api/task/{thread_id}", headers=_identity("owner", "tenant-b")).status_code
        == 404
    )
    snapshot = client.get(f"/api/task/{thread_id}", headers=owner)
    assert snapshot.status_code == 200
    assert snapshot.json()["tenant_id"] == "tenant-a"

    mismatch = client.get("/api/preferences/owner", headers=_identity("other", "tenant-a"))
    assert mismatch.status_code == 403


def test_rate_limit_returns_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")

    first = client.get("/api/task/missing")
    second = client.get("/api/task/missing")
    assert first.status_code == 404
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1


def test_release_drain_rejects_new_tasks(client: TestClient) -> None:
    server.runtime_control.begin_drain()
    response = client.post(
        "/api/task",
        json={"query": "找一款耳机", "user_id": "drain-user", "upload_ids": []},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "runtime_draining"


def test_canary_gate_reports_non_selected_requests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RELEASE_CHANNEL", "canary")
    monkeypatch.setenv("RELEASE_TRAFFIC_PERCENT", "0")
    response = client.post(
        "/api/task",
        headers={"X-Request-ID": "canary-outside"},
        json={"query": "找一款耳机", "user_id": "canary-user", "upload_ids": []},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "release_not_selected"


@pytest.mark.asyncio
async def test_provider_retry_and_circuit_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("PROVIDER_RETRY_BACKOFF_SECONDS", "0.01")
    monkeypatch.setenv("PROVIDER_RETRY_BACKOFF_MAX_SECONDS", "0.01")
    monkeypatch.setenv("PROVIDER_CIRCUIT_FAILURE_THRESHOLD", "1")
    registry = get_provider_resilience()
    registry.reset()
    calls = 0

    async def failing() -> None:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("provider timeout")

    with pytest.raises(httpx.ReadTimeout):
        await registry.execute("amazon", failing)
    assert calls == 3
    with pytest.raises(ProviderCircuitOpenError):
        await registry.execute("amazon", failing)
    assert calls == 3


@pytest.mark.asyncio
async def test_dispatch_has_global_child_concurrency_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_CHILDREN", "1")
    active = 0
    max_active = 0

    class Monitor:
        async def emit(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    async def worker(_demand: dict[str, Any]) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0)
            return "ok"
        finally:
            active -= 1

    with thread_scope("cap-parent", tmp_path):
        result = await dispatch_tool(
            [
                {"platform": "amazon", "query": "耳机"},
                {"platform": "ebay", "query": "耳机"},
            ],
            worker,
            Monitor(),  # type: ignore[arg-type]
        )
    assert result == ["ok", "ok"]
    assert max_active == 1


class _FakeSpan:
    def __init__(self, name: str, metadata: dict[str, Any]) -> None:
        self.name = name
        self.metadata = metadata
        self.children: list[_FakeSpan] = []
        self.output: dict[str, Any] | None = None
        self.ended = False

    def span(self, *, name: str, metadata: dict[str, Any]) -> _FakeSpan:
        child = _FakeSpan(name, metadata)
        self.children.append(child)
        return child

    def update(self, *, output: dict[str, Any]) -> None:
        self.output = output

    def end(self) -> None:
        self.ended = True


class _FakeClient:
    def __init__(self) -> None:
        self.trace_root: _FakeSpan | None = None

    def trace(self, *, id: str, name: str, metadata: dict[str, Any]) -> _FakeSpan:
        del id
        self.trace_root = _FakeSpan(name, metadata)
        return self.trace_root

    def flush(self) -> None:
        return None


def test_langfuse_fork_tools_are_nested_under_parent_trace() -> None:
    observer = object.__new__(LangFuseObserver)
    observer._client = _FakeClient()
    observer._traces = {}
    observer._child_spans = {}

    observer.start_trace("parent", query_length=4, data_mode="sandbox")
    observer.start_child_trace(
        "parent",
        "sub-1",
        platform="amazon",
        demand_keys=["platform", "query"],
        fork_depth=1,
    )
    observer.tool_span("sub-1", name="item_search", duration_ms=12, status="ok", route="rules")
    observer.end_child_trace("sub-1", status="ok")

    root = observer._client.trace_root
    assert root is not None
    assert root.children[0].metadata["parent_thread_id"] == "parent"
    assert root.children[0].children[0].metadata["thread_id"] == "sub-1"
    assert root.children[0].ended is True
