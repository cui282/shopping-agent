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

from app.recall.category_kb import CategoryCard


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="reviewed CategoryCard JSONL")
    parser.add_argument("--opensearch-url", default=os.getenv("OPENSEARCH_URL", ""))
    parser.add_argument(
        "--index", default=os.getenv("OPENSEARCH_CATEGORY_INDEX", "shopping_agent_category_kb")
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


def main() -> int:
    args = _args()
    if not args.opensearch_url:
        raise SystemExit("OPENSEARCH_URL or --opensearch-url is required")
    cards = _load_cards(args.input)
    if not cards:
        raise SystemExit("input contains no CategoryCard records")
    auth = None
    username = os.getenv("OPENSEARCH_USERNAME", "").strip()
    if username:
        auth = (username, os.getenv("OPENSEARCH_PASSWORD", ""))
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
