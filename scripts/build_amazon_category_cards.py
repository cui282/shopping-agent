"""Build structured CategoryCards from the local Amazon product metadata projection.

This is an offline, historical knowledge-card builder. It does not claim current bestseller,
price, inventory, shipping, or tax facts. The generated JSONL is suitable for the existing
``scripts/build_category_kb.py`` OpenSearch bulk indexer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import HashingVectorizer

from app.recall.category_kb import CategoryCard, CategoryCardContent


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/product_index/electronics-hash/products.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/category_cards/amazon_electronics.jsonl"),
    )
    parser.add_argument("--max-categories", type=int, default=100)
    parser.add_argument(
        "--embedding-mode",
        choices=("hashing", "sentence-transformer"),
        default=os.getenv("CATEGORY_EMBEDDING_MODE", "sentence-transformer"),
    )
    parser.add_argument(
        "--model",
        default=os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        ),
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=int(os.getenv("EMBEDDING_DIMENSION", "384")),
    )
    return parser.parse_args()


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _card_id(category: str, card_type: str) -> str:
    digest = hashlib.sha256(f"{category}|{card_type}".encode()).hexdigest()[:16]
    return f"amazon-electronics-{card_type}-{digest}"


def _encode(texts: list[str], *, mode: str, model_name: str, dimension: int) -> list[list[float]]:
    if mode == "hashing":
        vectorizer = HashingVectorizer(
            n_features=dimension,
            analyzer="char",
            ngram_range=(3, 5),
            alternate_sign=False,
            norm="l2",
            lowercase=True,
        )
        return vectorizer.transform(texts).toarray().tolist()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit("install the index extra first: uv sync --extra index") from exc
    model = SentenceTransformer(model_name)
    vectors = model.encode(
        texts,
        batch_size=min(64, max(1, len(texts))),
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    if getattr(vectors, "ndim", 0) != 2 or vectors.shape[1] != dimension:
        actual = vectors.shape[1] if getattr(vectors, "ndim", 0) == 2 else "unknown"
        raise SystemExit(f"embedding model returned dimension {actual}; expected {dimension}")
    return [[float(value) for value in vector] for vector in vectors]


def _category_cards(
    records: list[dict[str, Any]],
    max_categories: int,
    *,
    embedding_mode: str,
    model_name: str,
    dimension: int,
) -> list[CategoryCard]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        for category in record.get("categories", []):
            if isinstance(category, str) and category.strip():
                grouped[category.strip()].append(record)
    categories = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:max_categories]
    cards: list[CategoryCard] = []
    pending: list[tuple[str, str, str, dict[str, Any], list[str]]] = []
    for category, items in categories:
        brands = Counter(
            record["brand"]
            for record in items
            if isinstance(record.get("brand"), str) and record["brand"]
        )
        brand_distribution = {
            brand: round(count / max(1, sum(brands.values())), 4)
            for brand, count in brands.most_common(12)
        }
        evidence = [record["asin"] for record in items[:16] if record.get("asin")]
        attribute_structured = {
            "components": ["品牌", "商品描述", "历史目录属性"],
            "bestsellers": [],
            "attributes": [{"name": "brand", "distribution": brand_distribution}],
            "price_tiers": [],
            "confidence": min(0.95, 0.45 + min(len(items), 1000) / 2000),
        }
        attribute_summary = (
            f"Amazon Electronics 历史目录中，{category} 共收录 {len(items)} 个商品；"
            f"品牌分布靠前的是：{', '.join(brand_distribution) or '未提供品牌'}。"
        )
        pending.append((category, "attribute", attribute_summary, attribute_structured, evidence))

        ranked = sorted(
            items,
            key=lambda record: (
                min(
                    (
                        int(value)
                        for value in (record.get("sales_rank") or {}).values()
                        if str(value).isdigit()
                    ),
                    default=10**12,
                ),
                record.get("title", ""),
            ),
        )[:8]
        bestseller_structured = {
            "components": [],
            "bestsellers": [
                {
                    "name": record["title"],
                    "typical_price_cny": None,
                    "why_popular": "历史目录中的 salesRank 候选；不是实时销量",
                }
                for record in ranked
            ],
            "attributes": [],
            "price_tiers": [],
            "confidence": min(0.9, 0.4 + min(len(items), 1000) / 2000),
        }
        bestseller_summary = (
            f"{category} 的历史目录候选包括："
            + "；".join(record["title"] for record in ranked[:5])
            + "。排序依据是历史 salesRank，不能代表当前热销。"
        )
        pending.append(
            (category, "bestseller", bestseller_summary, bestseller_structured, evidence)
        )

    vectors = _encode(
        [item[2] for item in pending],
        mode=embedding_mode,
        model_name=model_name,
        dimension=dimension,
    )
    for (category, card_type, summary, structured, evidence), vector in zip(pending, vectors):
        cards.append(
            CategoryCard(
                card_id=_card_id(category, card_type),
                category=category,
                card_type=card_type,
                summary=summary,
                raw_evidence=evidence,
                last_updated="2015-09-24T00:00:00Z",
                confidence=float(structured["confidence"]),
                structured=CategoryCardContent.model_validate(structured),
                embedding=[float(value) for value in vector],
            )
        )
    return cards


def main() -> int:
    args = _args()
    if args.max_categories < 1 or args.dimension < 1:
        raise SystemExit("--max-categories and --dimension must be positive")
    records = _load(args.input)
    cards = _category_cards(
        records,
        args.max_categories,
        embedding_mode=args.embedding_mode,
        model_name=args.model,
        dimension=args.dimension,
    )
    if not cards:
        raise SystemExit("no category cards could be generated")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for card in cards:
            handle.write(json.dumps(card.model_dump(mode="json"), ensure_ascii=False))
            handle.write("\n")
    print(f"wrote {len(cards)} CategoryCards to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
