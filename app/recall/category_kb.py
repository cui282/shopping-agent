"""Typed CategoryInsight knowledge cards and their OpenSearch source projection."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas import StrictModel


class CategoryCardContent(StrictModel):
    """The compressed, structured fields consumed by CategoryInsight."""

    components: list[str] = Field(default_factory=list)
    bestsellers: list[dict[str, Any]] = Field(default_factory=list)
    attributes: list[dict[str, Any]] = Field(default_factory=list)
    price_tiers: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class CategoryCard(StrictModel):
    """One reviewed knowledge card; it is not a raw marketplace listing."""

    card_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    category: str = Field(min_length=1, max_length=400)
    card_type: Literal["bestseller", "attribute", "price_range"]
    summary: str = Field(min_length=1, max_length=4000)
    raw_evidence: list[str] = Field(default_factory=list, max_length=16)
    last_updated: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)
    structured: CategoryCardContent
    embedding: list[float] | None = Field(default=None, min_length=1)

    def opensearch_source(self) -> dict[str, Any]:
        """Return only the fields accepted by the category index mapping."""

        return self.model_dump(mode="json", exclude_none=True)


__all__ = ["CategoryCard", "CategoryCardContent"]
