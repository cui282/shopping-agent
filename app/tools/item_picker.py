from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from app.recall.category_reranker import HTTPTextReranker
from app.schemas import (
    ItemPickerOutput,
    LandedCost,
    ProviderMetadata,
    RememberedPreference,
    ShippingCalcOutput,
    ShoppingPlan,
)
from app.tools.decision_engine import decision_engine


def _offer_text(offer: LandedCost) -> str:
    """Keep the second-stage passage grounded in normalized offer evidence."""

    parts = [
        offer.title,
        f"platform: {offer.platform}",
        f"price_cny: {offer.price_cny:.2f}",
        f"landed_cny: {offer.landed_cny:.2f}",
        f"eta_days: {offer.eta_days}",
    ]
    for mapping in (offer.attributes, offer.variant_attributes):
        parts.extend(f"{key}: {value}" for key, value in mapping.items())
    return " | ".join(parts)[:4000]


async def _rerank_offers(
    query: str | None,
    offers: Sequence[LandedCost],
) -> tuple[dict[str, float], ProviderMetadata]:
    provider = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3").strip()
    if not query or not query.strip() or not offers:
        return {}, ProviderMetadata(source="computed", provider="item-picker")
    if not os.getenv("RERANKER_ENDPOINT", "").strip():
        return {}, ProviderMetadata(
            source="computed",
            provider=provider or "bge-reranker-v2-m3",
            status="degraded",
            fallback_reason="RERANKER_ENDPOINT is not configured; deterministic picker order remains active",
        )
    try:
        values = await HTTPTextReranker().score(
            query,
            [_offer_text(offer) for offer in offers],
        )
        if len(values) != len(offers):
            raise ValueError("reranker response must contain one score per offer")
        scores = {
            offer.item_id: max(0.0, min(1.0, float(score))) for offer, score in zip(offers, values)
        }
        return scores, ProviderMetadata(
            source="computed",
            provider=provider or "bge-reranker-v2-m3",
        )
    except Exception as exc:  # noqa: BLE001 - deterministic hard-constraint path remains usable
        return {}, ProviderMetadata(
            source="computed",
            provider=provider or "bge-reranker-v2-m3",
            status="degraded",
            fallback_reason=f"reranker failed: {type(exc).__name__}; deterministic picker order remains active",
        )


async def item_picker(
    shipping: ShippingCalcOutput,
    plan: ShoppingPlan,
    max_items: int = 3,
    remembered_preferences: RememberedPreference | Mapping[str, Any] | None = None,
    query: str | None = None,
) -> ItemPickerOutput:
    """Apply hard constraints, then rerank eligible offers for the final shortlist."""

    rerank_scores, provider = await _rerank_offers(query, shipping.items)
    picks = decision_engine(
        plan,
        remembered_preferences,
        shipping.items,
        max_items=max_items,
        rerank_scores=rerank_scores,
    )
    return picks.model_copy(update={"provider": provider})
