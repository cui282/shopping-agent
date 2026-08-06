from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AGENT_MODE", "rules")
os.environ.setdefault("SANDBOX_MODE", "true")
os.environ.setdefault("ALLOW_FIXTURE_FALLBACK", "false")
os.environ.setdefault("STORE_BACKEND", "memory")

from app.api import server
from app.api.connection import manager
from app.memory.store import InMemoryPreferenceStore


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AGENT_MODE", "rules")
    monkeypatch.setenv("SANDBOX_MODE", "true")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "false")
    monkeypatch.setenv("ALLOW_RULES_FALLBACK", "true")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setenv("UPLOAD_ROOT", str(tmp_path / "uploaded"))
    for prefix in ("AMAZON", "SHOPEE", "ALIEXPRESS", "EBAY"):
        monkeypatch.delenv(f"{prefix}_API_ENDPOINT", raising=False)
        monkeypatch.delenv(f"{prefix}_API_KEY", raising=False)
    monkeypatch.delenv("OPENSEARCH_URL", raising=False)
    server.records.clear()
    server.task_locks.clear()
    manager.active.clear()
    manager._events.clear()
    manager._generations.clear()
    manager._discarded.clear()
    server.preference_store = InMemoryPreferenceStore()
    yield
    for record in list(server.records.values()):
        if not record.task.done():
            record.task.cancel()


@pytest.fixture
def client():
    with TestClient(server.app) as test_client:
        yield test_client
