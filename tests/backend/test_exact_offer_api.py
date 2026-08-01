from __future__ import annotations


def wait_for_result(client, thread_id: str) -> dict:
    with client.websocket_connect(f"/ws/{thread_id}") as websocket:
        while True:
            event = websocket.receive_json()
            if event.get("event") == "task_result":
                return event["data"]
            if event.get("event") == "error":
                raise AssertionError(event)


def test_exact_offer_result_exposes_mode_matching_offers_and_alternatives(client) -> None:
    started = client.post(
        "/api/task",
        json={
            "query": "比价同款耳机，比较不同平台价格",
            "user_id": "exact-api-user",
            "upload_ids": [],
        },
    )
    assert started.status_code == 202

    result = wait_for_result(client, started.json()["thread_id"])

    assert result["mode"] == "exact_offer_comparison"
    assert result["matching_offers"] == []
    assert result["comparison"] == []
    assert len(result["alternative_candidates"]) == 12
    assert all(
        item["identity_evidence"]["decision"] == "alternative_candidate"
        for item in result["alternative_candidates"]
    )
    assert "Alternative Candidate" in result["final_answer"]


def test_product_research_result_keeps_different_fixture_products_comparable(client) -> None:
    started = client.post(
        "/api/task",
        json={
            "query": "找一款耳机，比较不同产品",
            "user_id": "product-research-api-user",
            "upload_ids": [],
        },
    )
    assert started.status_code == 202

    result = wait_for_result(client, started.json()["thread_id"])

    assert result["mode"] == "product_research"
    assert result["matching_offers"]
    assert result["alternative_candidates"] == []
    assert result["comparison"]
