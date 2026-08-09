from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import main_agent
from app.api import server
from app.schemas import (
    Candidate,
    ItemSearchOutput,
    ProviderMetadata,
    TaskSnapshot,
)


def _wait_for_terminal(client: TestClient, thread_id: str, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/task/{thread_id}").json()
        if snapshot["status"] not in {"running", "awaiting_clarification"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"task {thread_id} did not become terminal")


def _start_completed(
    client: TestClient,
    query: str = "预算 1200 元，找一款轻便降噪耳机",
) -> dict[str, Any]:
    response = client.post(
        "/api/task",
        json={"query": query, "user_id": "report-user", "upload_ids": []},
    )
    assert response.status_code == 202
    return _wait_for_terminal(client, response.json()["thread_id"])


def _report_bytes(client: TestClient, thread_id: str, file: dict[str, Any]) -> bytes:
    response = client.get(f"/api/files/{thread_id}/{file['name']}")
    assert response.status_code == 200
    return response.content


def test_three_reports_share_one_typed_snapshot_and_stable_delivery_contract(
    client: TestClient,
) -> None:
    snapshot = _start_completed(client)
    result = snapshot["result"]
    files = result["files"]

    assert {item["format"] for item in files} == {"markdown", "json", "pdf"}
    assert {item["name"] for item in files} == {
        "shopping-report.md",
        "shopping-report.json",
        "shopping-report.pdf",
    }
    assert {item["file_id"] for item in files} == {
        f"{snapshot['snapshot_id']}:markdown",
        f"{snapshot['snapshot_id']}:json",
        f"{snapshot['snapshot_id']}:pdf",
    }
    assert snapshot["report_references"] == files
    report_event = next(
        event for event in snapshot["events"] if event["event"] == "report_generated"
    )
    assert report_event["data"]["files"] == files
    assert snapshot["events"][-1]["event"] == "task_result"
    assert [event["event"] for event in server.manager.history(snapshot["thread_id"])[-2:]] == [
        "report_generated",
        "task_result",
    ]

    listed = client.get(f"/api/task/{snapshot['thread_id']}/reports")
    assert listed.status_code == 200
    assert listed.json()["files"] == files
    assert listed.json()["snapshot_effective_at"] == snapshot["updated_at"]

    json_file = next(item for item in files if item["format"] == "json")
    json_report = json.loads(_report_bytes(client, snapshot["thread_id"], json_file))
    markdown = _report_bytes(
        client,
        snapshot["thread_id"],
        next(item for item in files if item["format"] == "markdown"),
    ).decode("utf-8")
    assert json_report["snapshot_id"] == snapshot["snapshot_id"]
    assert json_report["snapshot_effective_at"] == snapshot["updated_at"]
    assert json_report["query"] == snapshot["query"]
    assert json_report["resolved_intent"] == snapshot["resolved_intent"]
    assert json_report["mode"] == result["mode"]
    assert json_report["working_assumptions"] == result["working_assumptions"]
    assert json_report["applied_preferences"] == result["applied_preferences"]
    assert json_report["task_overrides"] == result["task_overrides"]
    assert json_report["ranking_profile"] == result["ranking_profile"]
    assert json_report["product_evidence"] == result["product_evidence"]
    assert json_report["recommendations"] == result["recommendations"]
    assert json_report["notices"]
    assert all(notice["message"] in markdown for notice in json_report["notices"])
    assert "Working Assumption" in markdown
    assert "Ranking Profile" in markdown
    assert "不是 checkout guarantee" in markdown

    for file in files:
        response = client.get(f"/api/files/{snapshot['thread_id']}/{file['name']}")
        assert response.headers["content-type"].startswith(file["content_type"])
        assert "attachment" in response.headers["content-disposition"]
        assert file["name"] in response.headers["content-disposition"]
        assert response.headers["x-report-id"] == file["file_id"]

    pypdf = pytest.importorskip("pypdf")
    pdf_file = next(item for item in files if item["format"] == "pdf")
    reader = pypdf.PdfReader(_write_temp_pdf(client, snapshot["thread_id"], pdf_file))
    assert len(reader.pages) >= 1
    pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "购物研究报告" in pdf_text
    assert "Research Snapshot" in pdf_text
    assert "checkout guarantee" in pdf_text
    assert all(notice["code"] in pdf_text for notice in json_report["notices"])


def test_task_completes_when_ttf_pdf_font_is_unavailable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reportlab.pdfbase import ttfonts

    def reject_ttf_font(*_args: Any, **_kwargs: Any) -> Any:
        raise ttfonts.TTFError("font unavailable in container")

    monkeypatch.setattr(ttfonts, "TTFont", reject_ttf_font)

    snapshot = _start_completed(client)

    assert snapshot["status"] == "completed"
    assert any(file["format"] == "pdf" for file in snapshot["result"]["files"])


def _write_temp_pdf(client: TestClient, thread_id: str, file: dict[str, Any]) -> Path:
    destination = Path("/tmp") / f"{thread_id}-report-test.pdf"
    destination.write_bytes(_report_bytes(client, thread_id, file))
    return destination


def test_report_generation_is_read_only_for_research_and_rebuilds_deterministically(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _start_completed(client)
    before = {
        file["file_id"]: _report_bytes(client, snapshot["thread_id"], file)
        for file in snapshot["result"]["files"]
    }
    missing = next(item for item in snapshot["result"]["files"] if item["format"] == "pdf")
    (Path(server.output_root()) / snapshot["thread_id"] / missing["name"]).unlink()
    calls = {"gateway": 0, "recall": 0, "fx": 0}

    async def fail_research(*_args: Any, **_kwargs: Any) -> Any:
        calls["gateway"] += 1
        raise AssertionError("report generation must not start research")

    def fail_recall(*_args: Any, **_kwargs: Any) -> Any:
        calls["recall"] += 1
        raise AssertionError("report generation must not recall preferences")

    def fail_fx(*_args: Any, **_kwargs: Any) -> Any:
        calls["fx"] += 1
        raise AssertionError("report generation must not refresh exchange rates")

    monkeypatch.setattr(server, "run_agent", fail_research)
    monkeypatch.setattr(server.preference_store, "get", fail_recall)
    monkeypatch.setattr(importlib.import_module("app.tools.price_compare"), "_rates", fail_fx)

    rebuilt = client.post(f"/api/task/{snapshot['thread_id']}/reports")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["idempotent"] is True
    assert calls == {"gateway": 0, "recall": 0, "fx": 0}

    for file in snapshot["result"]["files"]:
        assert _report_bytes(client, snapshot["thread_id"], file) == before[file["file_id"]]

    server.records.clear()
    restarted = client.get(f"/api/reports/{snapshot['thread_id']}")
    assert restarted.status_code == 200
    assert restarted.json()["files"] == snapshot["result"]["files"]
    assert calls == {"gateway": 0, "recall": 0, "fx": 0}


def test_reports_reject_unfinished_tasks_missing_files_and_path_traversal(
    client: TestClient,
) -> None:
    running = TaskSnapshot(
        snapshot_id="unfinished-report",
        thread_id="unfinished-report",
        status="running",
        query="找耳机",
        user_id="report-user",
        data_mode="sandbox",
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:00Z",
    )
    server._persist_snapshot(running)
    assert client.get("/api/reports/unfinished-report").status_code == 409
    assert client.post("/api/task/unfinished-report/reports").status_code == 409
    assert client.get("/api/files/unfinished-report/shopping-report.pdf").status_code == 404

    snapshot = _start_completed(client)
    assert client.get(f"/api/files/{snapshot['thread_id']}/task.json").status_code == 404
    assert client.get(f"/api/files/{snapshot['thread_id']}/%2E%2E%2Ftask.json").status_code in {
        400,
        404,
    }


def test_reports_keep_partial_and_no_match_notices(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://gateway.example/amazon")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")

    async def partial_search(query, platform, top_k=20, user_id=None):
        del query, top_k, user_id
        if platform == "amazon":
            return ItemSearchOutput(
                platform="amazon",
                candidates=[
                    Candidate(
                        item_id="partial-evidence",
                        platform="amazon",
                        title="Partial live headphones",
                        price=100,
                        currency="USD",
                        source="live",
                    )
                ],
                total_recall=1,
                truncated=False,
                provider=ProviderMetadata(source="live", provider="amazon-feed"),
            )
        raise RuntimeError("provider request failed")

    original_search = main_agent.item_search
    monkeypatch.setattr(main_agent, "item_search", partial_search)
    partial = _start_completed(client, "找一款降噪耳机")
    partial_json = next(item for item in partial["result"]["files"] if item["format"] == "json")
    partial_report = json.loads(_report_bytes(client, partial["thread_id"], partial_json))
    partial_markdown = _report_bytes(
        client,
        partial["thread_id"],
        next(item for item in partial["result"]["files"] if item["format"] == "markdown"),
    ).decode("utf-8")
    partial_pdf = _write_temp_pdf(
        client,
        partial["thread_id"],
        next(item for item in partial["result"]["files"] if item["format"] == "pdf"),
    )
    assert partial["result"]["result_kind"] == "partial"
    assert any(notice["code"] == "partial_result" for notice in partial_report["notices"])
    assert any("reason=not_configured" in notice["message"] for notice in partial_report["notices"])
    assert "partial_result" in partial_markdown
    assert "partial_result" in "\n".join(
        page.extract_text() or ""
        for page in pytest.importorskip("pypdf").PdfReader(partial_pdf).pages
    )

    monkeypatch.setattr(main_agent, "item_search", original_search)
    monkeypatch.setenv("SANDBOX_MODE", "true")
    no_match = _start_completed(client, "预算 1 元，找一款耳机，不要塑料的")
    no_match_json = next(item for item in no_match["result"]["files"] if item["format"] == "json")
    no_match_report = json.loads(_report_bytes(client, no_match["thread_id"], no_match_json))
    no_match_markdown = _report_bytes(
        client,
        no_match["thread_id"],
        next(item for item in no_match["result"]["files"] if item["format"] == "markdown"),
    ).decode("utf-8")
    no_match_pdf = _write_temp_pdf(
        client,
        no_match["thread_id"],
        next(item for item in no_match["result"]["files"] if item["format"] == "pdf"),
    )
    assert no_match["result"]["match_status"] == "no_match"
    assert any(notice["code"] == "no_match" for notice in no_match_report["notices"])
    assert no_match_report["exclusions"]
    assert any(notice["code"] == "exclusion" for notice in no_match_report["notices"])
    assert "no_match" in no_match_markdown
    assert "no_match" in "\n".join(
        page.extract_text() or ""
        for page in pytest.importorskip("pypdf").PdfReader(no_match_pdf).pages
    )
