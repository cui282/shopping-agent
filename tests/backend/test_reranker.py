from __future__ import annotations

from fastapi.testclient import TestClient

import app.reranker.server as reranker_server


class FakeCrossEncoder:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def predict(self, pairs, **kwargs):
        assert pairs[0][0] == "无线耳机"
        assert kwargs["show_progress_bar"] is False
        assert kwargs["convert_to_numpy"] is True
        return [-2.0, 2.0]


def test_bge_reranker_scores_text_and_product_candidates(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setenv("RERANKER_DEVICE", "cpu")
    monkeypatch.setattr(reranker_server, "_model", lambda: FakeCrossEncoder())

    response = TestClient(reranker_server.app).post(
        "/score",
        json={
            "query": "无线耳机",
            "candidates": [
                "咖啡机研磨和萃取",
                {"title": "降噪耳机", "attributes": {"style": "通勤"}},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "BAAI/bge-reranker-v2-m3"
    assert len(payload["scores"]) == 2
    assert payload["scores"][0] < 0.5 < payload["scores"][1]


def test_bge_reranker_rejects_requested_model_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    response = TestClient(reranker_server.app).post(
        "/score",
        json={"model": "other-model", "query": "耳机", "candidates": ["耳机"]},
    )

    assert response.status_code == 400
    assert "bge-reranker-v2-m3" in response.json()["detail"]


def test_bge_reranker_health_reports_configured_model(monkeypatch) -> None:
    monkeypatch.setattr(reranker_server, "_model", lambda: FakeCrossEncoder())
    response = TestClient(reranker_server.app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["model"] == "BAAI/bge-reranker-v2-m3"
