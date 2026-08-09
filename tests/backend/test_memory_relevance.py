from __future__ import annotations

import pytest

from app.memory.relevance import rank_relevant_preferences
from app.memory.store import InMemoryPreferenceStore


def test_relevance_keeps_only_explicit_query_related_preferences() -> None:
    ranked = rank_relevant_preferences(
        {
            "material_preferences": ["织物", "皮革"],
            "style_preferences": ["简约"],
            "soft_preferences": ["轻便"],
            "avoid": ["厚重"],
        },
        "找轻便的织物耳机",
        limit=2,
    )

    assert ranked == {"material_preferences": ["织物"], "soft_preferences": ["轻便"]}


@pytest.mark.asyncio
async def test_in_memory_store_uses_relevance_query() -> None:
    store = InMemoryPreferenceStore()
    await store.put(
        "user-1",
        {
            "material_preferences": ["织物", "皮革"],
            "style_preferences": ["简约"],
            "soft_preferences": [],
            "avoid": [],
        },
    )

    assert await store.read_relevant("user-1", "织物耳机", limit=1) == {
        "material_preferences": ["织物"]
    }
