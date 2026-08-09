from __future__ import annotations

import pytest

from app.eval.recall_metrics import (
    assert_release_gate,
    evaluate_recall,
    mrr,
    ndcg_at_k,
    recall_at_k,
)
from app.recall.category_kb import CategoryCard
from app.recall.category_norm import normalize_category
from app.recall.orchestrator import RecallAdapters, RecallOrchestrator
from app.recall.rag import extract_structured_card, summarize_card
from app.schemas import Candidate, CategoryInsightOutput, ProviderMetadata


def _candidate(item_id: str) -> Candidate:
    return Candidate(
        item_id=item_id,
        platform="amazon",
        title=item_id,
        price=100,
        currency="USD",
        source="live",
    )


def test_rag_extracts_only_structured_fields_with_document_provenance() -> None:
    structured, evidence = extract_structured_card(
        [
            {"id": "doc-1", "structured": {"components": ["降噪"], "confidence": 0.9}, "_score": 2},
        ]
    )

    components, bestsellers, attributes, tiers, confidence = summarize_card("耳机", structured)
    assert components == ["降噪"]
    assert bestsellers == []
    assert attributes == []
    assert tiers == []
    assert confidence == 0.9
    assert evidence[0].document_id == "doc-1"


def test_rag_rejects_missing_structured_documents() -> None:
    with pytest.raises(LookupError):
        extract_structured_card(
            [{"id": "raw", "text": "untyped facts"}, {"id": "empty", "structured": {}}]
        )


def test_category_card_projects_structured_fields_for_opensearch() -> None:
    card = CategoryCard(
        card_id="headphones-1",
        category="耳机",
        card_type="attribute",
        summary="降噪耳机常见关注点",
        raw_evidence=["reviewed source"],
        last_updated="2026-08-10T00:00:00Z",
        confidence=0.9,
        structured={"components": ["降噪"], "confidence": 0.9},
    )

    source = card.opensearch_source()
    assert source["structured"]["components"] == ["降噪"]
    assert "embedding" not in source


def test_category_aliases_and_recall_metrics_are_deterministic() -> None:
    assert normalize_category("  旅行收纳 ") == "旅行三件套"
    assert recall_at_k(["noise", "c-1"], ["c-1", "c-2"], 2) == 0.5
    assert mrr(["noise", "c-1"], ["c-1"]) == 0.5
    assert ndcg_at_k(["c-2", "c-1"], ["c-1", "c-2"], 2) < 1
    evaluation = evaluate_recall(
        [(["c-1", "c-2"], ["c-1", "c-2"]), (["noise"], ["c-3"])],
        k=2,
    )
    gate = assert_release_gate(evaluation, min_recall=0.4, min_mrr=0.4, min_ndcg=0.4)
    assert gate.passed is True
    assert evaluation.sample_count == 2


class FakeReranker:
    async def score(self, _query: str, candidates: list[Candidate]) -> list[float]:
        return [0.1 if candidate.item_id == "first" else 0.9 for candidate in candidates]


@pytest.mark.asyncio
async def test_optional_reranker_reorders_candidates_without_creating_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RERANKER_ENDPOINT", "http://reranker.test/score")
    candidates = [_candidate("first"), _candidate("second")]
    insight = CategoryInsightOutput(
        category="耳机",
        components=[],
        bestsellers=[],
        attributes=[],
        price_tiers=[],
        confidence=0.5,
        provider=ProviderMetadata(source="curated", provider="test"),
    )

    result = await RecallOrchestrator(
        adapters=RecallAdapters(reranker=FakeReranker()),
    ).recall("耳机", candidates, category_insight=insight, top_k=2)

    assert [candidate.item_id for candidate in result.candidates] == ["second", "first"]
    assert [candidate.item_id for candidate in candidates] == ["first", "second"]
    assert result.provenance.mode == "deterministic_fallback"
