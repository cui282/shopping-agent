"""Query-local ranking for explicit Remembered Preferences.

This is intentionally lexical and deterministic. It keeps preference retrieval independent from
the inference-only Query/Item dual tower, so a remembered preference never becomes an implicit
user embedding. A legacy User Tower, when explicitly enabled, receives only this filtered record.
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas import RememberedPreference

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-z0-9][a-z0-9+._-]*", re.IGNORECASE)


def _tokens(query: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_RE.finditer(query)}


def _score(value: str, query_tokens: set[str], field: str) -> tuple[int, int, str]:
    normalized = value.casefold()
    overlap = sum(1 for token in query_tokens if token in normalized or normalized in token)
    field_boost = 1 if field in {"avoid", "material_preferences"} else 0
    return overlap + field_boost, len(value), normalized


def rank_relevant_preferences(
    preferences: RememberedPreference | dict[str, Any] | None,
    query: str,
    *,
    limit: int = 5,
) -> dict[str, list[str]]:
    """Return at most ``limit`` explicit preference values ordered by query relevance."""

    model = (
        preferences
        if isinstance(preferences, RememberedPreference)
        else RememberedPreference.model_validate(
            {
                field: (preferences or {}).get(field, [])
                for field in RememberedPreference.model_fields
            }
        )
    )
    query_tokens = _tokens(query)
    ranked: list[tuple[tuple[int, int, str], str, str]] = []
    for field in RememberedPreference.model_fields:
        for value in getattr(model, field):
            if not isinstance(value, str) or not value.strip():
                continue
            ranked.append((_score(value, query_tokens, field), field, value.strip()))
    ranked.sort(key=lambda item: (-item[0][0], -item[0][1], item[0][2], item[1]))
    selected: dict[str, list[str]] = {field: [] for field in RememberedPreference.model_fields}
    for _, field, value in ranked[: max(0, limit)]:
        selected[field].append(value)
    return {field: values for field, values in selected.items() if values}


__all__ = ["rank_relevant_preferences"]
