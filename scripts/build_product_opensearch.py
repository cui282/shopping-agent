"""Build a versioned OpenSearch product index from normalized JSONL snapshots.

The input files are offline runtime artifacts. This command never crawls a marketplace and swaps
the read alias only after the complete target index has been refreshed successfully.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from app.data.product_document import normalize_product_record

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[1]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        required=True,
        help="JSONL snapshot path; repeat for multiple marketplaces",
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=("live", "fixture"),
        help="source for each input path; omit to use the record's source or live",
    )
    parser.add_argument(
        "--snapshot-at", default="", help="default ISO snapshot timestamp for inputs"
    )
    parser.add_argument("--expires-at", default="", help="default ISO expiry timestamp for inputs")
    parser.add_argument("--provider", default="", help="default provenance provider for inputs")
    parser.add_argument("--upstream-source", default="", help="default upstream source for inputs")
    parser.add_argument(
        "--opensearch-url", default=os.getenv("OPENSEARCH_URL", ""), help="OpenSearch base URL"
    )
    parser.add_argument(
        "--alias", default=os.getenv("OPENSEARCH_PRODUCT_INDEX", "shopping_agent_products")
    )
    parser.add_argument(
        "--target-index", default="", help="concrete index name; defaults to a UTC version"
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=REPO_ROOT / "docker/opensearch/product-index.json",
        help="index mapping JSON",
    )
    parser.add_argument(
        "--embedding-mode",
        choices=("hashing", "sentence-transformer"),
        default=os.getenv("PRODUCT_SEARCH_EMBEDDING_MODE", "sentence-transformer"),
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
        default=int(os.getenv("PRODUCT_SEARCH_EMBEDDING_DIMENSION", "384")),
    )
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--max-items", type=int, default=0, help="0 means all records")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/product_index/opensearch/product-manifest.json"),
    )
    return parser.parse_args()


def _auth() -> tuple[str, str] | None:
    username = os.getenv("OPENSEARCH_USERNAME", "").strip()
    return (username, os.getenv("OPENSEARCH_PASSWORD", "")) if username else None


def _load_mapping(path: Path, dimension: int) -> dict[str, Any]:
    mapping = json.loads(path.read_text(encoding="utf-8"))
    configured_dimension = (
        mapping.get("mappings", {}).get("properties", {}).get("embedding", {}).get("dimension")
    )
    if configured_dimension != dimension:
        raise SystemExit(
            f"embedding mapping dimension {configured_dimension} does not match --dimension {dimension}"
        )
    return mapping


def _read_records(
    paths: list[Path],
    sources: list[str] | None,
    max_items: int,
    *,
    snapshot_at: str,
    expires_at: str,
    provider: str,
    upstream_source: str,
) -> list[dict[str, Any]]:
    if sources and len(sources) != len(paths):
        raise SystemExit("--source must be repeated once for every --input")
    by_item: dict[str, dict[str, Any]] = {}
    for position, path in enumerate(paths):
        if max_items and len(by_item) >= max_items:
            break
        if not path.is_file():
            raise SystemExit(f"snapshot does not exist: {path}")
        default_source = sources[position] if sources else "live"
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(raw, dict):
                continue
            record = normalize_product_record(
                raw,
                default_source=default_source,
                default_provider=provider or "marketplace-snapshot",
                snapshot_at=snapshot_at or None,
                expires_at=expires_at or None,
            )
            if record is not None and upstream_source and "upstream_source" not in record:
                record["upstream_source"] = upstream_source
            if record is not None:
                by_item[record["item_id"]] = record
            if max_items and len(by_item) >= max_items:
                break
    return list(by_item.values())


def _encode(
    records: list[dict[str, Any]], mode: str, model_name: str, dimension: int, batch_size: int
):
    try:
        import numpy as np
    except ImportError as exc:
        raise SystemExit("install the index extra first: uv sync --extra index") from exc
    texts = [str(record.get("searchable_text") or record["title"]) for record in records]
    if mode == "hashing":
        from sklearn.feature_extraction.text import HashingVectorizer

        vectorizer = HashingVectorizer(
            n_features=dimension,
            analyzer="char",
            ngram_range=(3, 5),
            alternate_sign=False,
            norm="l2",
            lowercase=True,
        )
        return vectorizer.transform(texts).toarray().astype("float32")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit("install the index extra first: uv sync --extra index") from exc
    model = SentenceTransformer(model_name)
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    vectors = np.asarray(vectors, dtype="float32")
    if vectors.ndim != 2 or vectors.shape[1] != dimension:
        raise SystemExit(
            f"embedding model returned dimension {vectors.shape[1] if vectors.ndim == 2 else 'unknown'}; "
            f"expected {dimension}"
        )
    return vectors


def _bulk_body(index: str, records: list[dict[str, Any]], vectors: Any) -> str:
    lines: list[str] = []
    for record, vector in zip(records, vectors):
        source = {**record, "embedding": [float(value) for value in vector]}
        lines.append(json.dumps({"index": {"_index": index, "_id": record["item_id"]}}))
        lines.append(json.dumps(source, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def _alias_actions(
    alias: str, target_index: str, existing: list[str]
) -> dict[str, list[dict[str, str]]]:
    actions = [{"remove": {"index": index, "alias": alias}} for index in existing]
    actions.append({"add": {"index": target_index, "alias": alias}})
    return {"actions": actions}


def _source_hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main() -> int:
    args = _args()
    if not args.opensearch_url:
        raise SystemExit("OPENSEARCH_URL or --opensearch-url is required")
    if args.dimension < 1 or args.batch_size < 1 or args.max_items < 0:
        raise SystemExit("dimension and batch size must be positive; max-items cannot be negative")
    records = _read_records(
        args.input,
        args.source,
        args.max_items,
        snapshot_at=args.snapshot_at,
        expires_at=args.expires_at,
        provider=args.provider,
        upstream_source=args.upstream_source,
    )
    if not records:
        raise SystemExit("input snapshots contain no valid products")
    mapping = _load_mapping(args.mapping, args.dimension)
    vectors = _encode(records, args.embedding_mode, args.model, args.dimension, args.batch_size)
    target = args.target_index or f"{args.alias}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    base_url = args.opensearch_url.rstrip("/")
    auth = _auth()
    with httpx.Client(timeout=60, auth=auth) as client:
        existing: list[str] = []
        aliases = client.get(f"{base_url}/_alias/{args.alias}")
        if aliases.status_code == 200:
            payload = aliases.json()
            existing = list(payload.keys())
        elif aliases.status_code != 404:
            aliases.raise_for_status()
        bootstrap_concrete = (
            not args.target_index
            and not existing
            and client.head(f"{base_url}/{args.alias}").status_code == 200
        )
        if bootstrap_concrete:
            target = args.alias
        else:
            response = client.put(f"{base_url}/{target}", json=mapping)
            response.raise_for_status()
        for start in range(0, len(records), args.batch_size):
            chunk_records = records[start : start + args.batch_size]
            chunk_vectors = vectors[start : start + args.batch_size]
            response = client.post(
                f"{base_url}/_bulk",
                content=_bulk_body(target, chunk_records, chunk_vectors),
                headers={"content-type": "application/x-ndjson"},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise SystemExit(f"OpenSearch rejected a bulk chunk starting at item {start}")
        client.post(f"{base_url}/{target}/_refresh").raise_for_status()
        if not bootstrap_concrete:
            alias_response = client.post(
                f"{base_url}/_aliases",
                json=_alias_actions(args.alias, target, existing),
            )
            alias_response.raise_for_status()

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "alias": args.alias,
        "index": target,
        "item_count": len(records),
        "embedding_mode": args.embedding_mode,
        "model": args.model if args.embedding_mode == "sentence-transformer" else None,
        "dimension": args.dimension,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_hashes": _source_hashes(args.input),
    }
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
