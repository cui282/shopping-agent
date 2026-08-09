from __future__ import annotations

import pytest

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
