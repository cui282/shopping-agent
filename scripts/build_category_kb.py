"""Bulk-index reviewed CategoryInsight cards into OpenSearch.

The input is an operator-owned JSONL export. This script does not crawl marketplaces, generate
cards, or fabricate embeddings; an optional embedding must already be present in each card.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from app.recall.category_kb import CategoryCard

load_dotenv()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="reviewed CategoryCard JSONL")
    parser.add_argument("--opensearch-url", default=os.getenv("OPENSEARCH_URL", ""))
    parser.add_argument(
        "--index", default=os.getenv("OPENSEARCH_CATEGORY_INDEX", "shopping_agent_category_kb")
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=int(os.getenv("EMBEDDING_DIMENSION", "384")),
        help="expected dimension for the configured OpenSearch knn_vector field",
    )
    return parser.parse_args()


def _load_cards(path: Path) -> list[CategoryCard]:
    cards: list[CategoryCard] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cards.append(CategoryCard.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid CategoryCard at line {line_number}") from exc
    return cards


def _bulk_body(cards: list[CategoryCard]) -> str:
    lines: list[str] = []
    for card in cards:
        lines.append(json.dumps({"index": {"_id": card.card_id}}, ensure_ascii=False))
        lines.append(json.dumps(card.opensearch_source(), ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _validate_embeddings(cards: list[CategoryCard], dimension: int) -> None:
    for card in cards:
        if card.embedding is not None and len(card.embedding) != dimension:
            raise ValueError(
                f"CategoryCard {card.card_id} embedding dimension {len(card.embedding)} "
                f"does not match expected {dimension}"
            )


def _index_dimension(payload: Any, index: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    index_payload = payload.get(index)
    if not isinstance(index_payload, dict) and len(payload) == 1:
        # GET /alias/_mapping returns the concrete index name rather than the alias.
        index_payload = next(iter(payload.values()))
    if not isinstance(index_payload, dict):
        return None
    mappings = index_payload.get("mappings")
    if not isinstance(mappings, dict):
        return None
    properties = mappings.get("properties")
    if not isinstance(properties, dict):
        return None
    embedding = properties.get("embedding")
    if not isinstance(embedding, dict):
        return None
    value = embedding.get("dimension")
    return int(value) if isinstance(value, int) else None


def main() -> int:
    args = _args()
    if not args.opensearch_url:
        raise SystemExit("OPENSEARCH_URL or --opensearch-url is required")
    if args.dimension < 1:
        raise SystemExit("--dimension must be positive")
    cards = _load_cards(args.input)
    if not cards:
        raise SystemExit("input contains no CategoryCard records")
    _validate_embeddings(cards, args.dimension)
    auth = None
    username = os.getenv("OPENSEARCH_USERNAME", "").strip()
    if username:
        auth = (username, os.getenv("OPENSEARCH_PASSWORD", ""))
    mapping_response = httpx.get(
        f"{args.opensearch_url.rstrip('/')}/{args.index}/_mapping",
        auth=auth,
        timeout=30,
    )
    mapping_response.raise_for_status()
    actual_dimension = _index_dimension(mapping_response.json(), args.index)
    if actual_dimension is not None and actual_dimension != args.dimension:
        raise SystemExit(
            f"OpenSearch category index dimension {actual_dimension} does not match "
            f"--dimension {args.dimension}; recreate the index before indexing"
        )
    response = httpx.post(
        f"{args.opensearch_url.rstrip('/')}/{args.index}/_bulk",
        content=_bulk_body(cards),
        headers={"content-type": "application/x-ndjson"},
        auth=auth,
        timeout=30,
    )
    response.raise_for_status()
    payload: Any = response.json()
    if payload.get("errors"):
        raise SystemExit("OpenSearch rejected one or more CategoryCard records")
    print(f"indexed {len(cards)} CategoryCard records into {args.index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
