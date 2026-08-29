"""Download Amazon metadata and build a local Faiss product index.

The generated files are runtime artifacts under ``data/`` and are intentionally not committed.
This index is an offline catalog artifact; it does not provide live price, stock, shipping, or tax
evidence for production research.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SOURCE_URL = (
    "https://snap.stanford.edu/data/amazon/productGraph/categoryFiles/meta_Electronics.json.gz"
)
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/datasets/amazon/meta_Electronics.json.gz"),
        help="local gzip JSONL path; downloaded from --source-url when absent",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/product_index/electronics"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--embedding-mode",
        choices=("sentence-transformer", "hashing"),
        default="sentence-transformer",
        help="semantic SentenceTransformer or deterministic lexical hashing baseline",
    )
    parser.add_argument("--hash-dim", type=int, default=384)
    parser.add_argument(
        "--max-items",
        type=int,
        default=100_000,
        help="maximum quality records to index; use 0 for every valid source record",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    return parser.parse_args()


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    print(f"Downloading {url}")
    urllib.request.urlretrieve(url, temporary)
    temporary.replace(destination)


def _parse_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        try:
            value = ast.literal_eval(line)
        except (SyntaxError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(item for item in (_text(part) for part in value) if item)
    return ""


def _categories(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for path in value:
        if isinstance(path, list):
            values.extend(_text(item) for item in path)
        else:
            values.append(_text(path))
    return list(dict.fromkeys(item for item in values if item))


def _price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _record(raw: dict[str, Any]) -> dict[str, Any] | None:
    asin = _text(raw.get("asin"))
    title = _text(raw.get("title"))
    if not asin or not title:
        return None
    categories = _categories(raw.get("categories"))
    description = _text(raw.get("description"))
    brand = _text(raw.get("brand"))
    price = _price(raw.get("price"))
    text_parts = [title, brand, " / ".join(categories), description]
    searchable_text = " | ".join(part for part in text_parts if part)
    return {
        "item_id": f"amazon:{asin}",
        "asin": asin,
        "platform": "amazon",
        "title": title,
        "brand": brand or None,
        "categories": categories,
        "description": description,
        "price": price,
        "currency": "USD" if price is not None else None,
        "image_url": _text(raw.get("imUrl")) or None,
        "sales_rank": raw.get("salesRank"),
        "related": raw.get("related", {}),
        "searchable_text": searchable_text[:4000],
    }


def _records(path: Path, max_items: int) -> Iterator[dict[str, Any]]:
    accepted = 0
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            value = _parse_line(line.strip())
            if value is None:
                continue
            record = _record(value)
            if record is None:
                continue
            yield record
            accepted += 1
            if max_items and accepted >= max_items:
                return


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def main() -> int:
    args = _args()
    if args.max_items < 0 or args.batch_size < 1:
        raise SystemExit("--max-items must be >= 0 and --batch-size must be positive")
    source = args.source
    if not source.exists():
        _download(args.source_url, source)

    records = list(_records(source, args.max_items))
    if not records:
        raise SystemExit("the source did not contain valid product records")

    texts = [record["searchable_text"] for record in records]
    try:
        import faiss
        import numpy as np
    except ImportError as exc:
        raise SystemExit("install the index extra first: uv sync --extra index") from exc

    if args.embedding_mode == "sentence-transformer":
        print(f"Encoding {len(records):,} products with {args.model}")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise SystemExit("install the index extra first: uv sync --extra index") from exc
        model = SentenceTransformer(args.model)
        embeddings = model.encode(
            texts,
            batch_size=args.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        vectors = np.asarray(embeddings, dtype="float32")
    else:
        if args.hash_dim < 32:
            raise SystemExit("--hash-dim must be at least 32")
        from sklearn.feature_extraction.text import HashingVectorizer

        print(
            f"Building {len(records):,} products with {args.hash_dim}-dimensional hashing vectors"
        )
        vectorizer = HashingVectorizer(
            n_features=args.hash_dim,
            analyzer="char",
            ngram_range=(3, 5),
            alternate_sign=False,
            norm="l2",
            lowercase=True,
        )
        vectors = vectorizer.transform(texts).toarray().astype("float32")
    if vectors.ndim != 2 or vectors.shape[0] != len(records):
        raise SystemExit("embedding output shape does not match product records")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    index_path = args.output_dir / "products.faiss"
    records_path = args.output_dir / "products.jsonl"
    manifest_path = args.output_dir / "manifest.json"

    index = faiss.IndexIDMap2(faiss.IndexFlatIP(vectors.shape[1]))
    ids = np.arange(len(records), dtype="int64")
    index.add_with_ids(vectors, ids)
    faiss.write_index(index, str(index_path))
    _write_records(records_path, records)

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "index_type": "faiss.IndexIDMap2(IndexFlatIP)",
        "embedding_mode": args.embedding_mode,
        "model": args.model if args.embedding_mode == "sentence-transformer" else None,
        "dimension": int(vectors.shape[1]),
        "metric": "inner_product_on_normalized_embeddings",
        "item_count": len(records),
        "source_url": args.source_url,
        "source_path": str(source),
        "source_sha256": source_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": str(records_path),
        "index": str(index_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
