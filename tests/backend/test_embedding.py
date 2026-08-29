from __future__ import annotations

from fastapi.testclient import TestClient

import app.embedding.server as embedding_server


class FakeEmbeddingModel:
    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, texts: list[str], **kwargs):
        assert kwargs["normalize_embeddings"] is True
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_embedding_service_supports_query_and_openai_contract(monkeypatch) -> None:
    monkeypatch.setattr(embedding_server, "_model", lambda: FakeEmbeddingModel())
    client = TestClient(embedding_server.app)

    query_response = client.post("/embed", json={"query": "无线耳机"})
    openai_response = client.post(
        "/v1/embeddings",
        json={"model": "sentence-transformers/test", "input": ["耳机", "咖啡机"]},
    )

    assert query_response.status_code == 200
    assert query_response.json()["embedding"] == [0.1, 0.2, 0.3]
    assert openai_response.status_code == 200
    assert openai_response.json()["object"] == "list"
    assert len(openai_response.json()["data"]) == 2
    assert len(openai_response.json()["data"][0]["embedding"]) == 3


def test_embedding_service_rejects_multiple_texts_on_query_route(monkeypatch) -> None:
    monkeypatch.setattr(embedding_server, "_model", lambda: FakeEmbeddingModel())
    response = TestClient(embedding_server.app).post("/embed", json={"input": ["耳机", "咖啡机"]})
    assert response.status_code == 422
