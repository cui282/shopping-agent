"""Read-only local Faiss catalog adapter for explicit Sandbox catalog search."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


class ProductCatalogError(RuntimeError):
    """The local product index cannot serve a query."""


class FaissProductCatalog:
    def __init__(self, index_dir: str | Path) -> None:
        self.index_dir = Path(index_dir).expanduser()
        manifest_path = self.index_dir / "manifest.json"
        records_path = self.index_dir / "products.jsonl"
        index_path = self.index_dir / "products.faiss"
        if not manifest_path.is_file() or not records_path.is_file() or not index_path.is_file():
            raise ProductCatalogError(
                f"product index is incomplete: expected {manifest_path}, {records_path}, and {index_path}"
            )
        try:
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.records = [
                json.loads(line)
                for line in records_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            import faiss
            import numpy as np
            from sklearn.feature_extraction.text import HashingVectorizer

            self._np = np
            self._index = faiss.read_index(str(index_path))
            if self.manifest.get("embedding_mode") != "hashing":
                raise ProductCatalogError(
                    "the local runtime supports the hashing product index; build a matching index"
                )
            dimension = int(self.manifest.get("dimension", 384))
            if dimension != 384:
                raise ProductCatalogError(f"unsupported hashing index dimension: {dimension}")
            self._vectorizer = HashingVectorizer(
                n_features=dimension,
                analyzer="char",
                ngram_range=(3, 5),
                alternate_sign=False,
                norm="l2",
                lowercase=True,
            )
        except ProductCatalogError:
            raise
        except Exception as exc:
            raise ProductCatalogError(
                f"failed to load local product index: {type(exc).__name__}"
            ) from exc
        if self._index.ntotal != len(self.records):
            raise ProductCatalogError("Faiss index count does not match product mapping count")

    def search(
        self, query: str, *, top_k: int = 20, min_score: float | None = None
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        vector = self._vectorizer.transform([query]).toarray().astype("float32")
        scores, ids = self._index.search(vector, max(1, min(top_k * 4, self._index.ntotal)))
        results: list[dict[str, Any]] = []
        for score, raw_id in zip(scores[0], ids[0]):
            index_id = int(raw_id)
            if index_id < 0 or index_id >= len(self.records):
                continue
            if min_score is not None and float(score) < min_score:
                continue
            record = dict(self.records[index_id])
            record["faiss_score"] = float(score)
            if record.get("price") is None:
                continue
            results.append(record)
            if len(results) >= top_k:
                break
        return results


@lru_cache(maxsize=4)
def get_product_catalog(index_dir: str) -> FaissProductCatalog:
    return FaissProductCatalog(index_dir)


def reset_product_catalog_cache() -> None:
    get_product_catalog.cache_clear()


__all__ = [
    "FaissProductCatalog",
    "ProductCatalogError",
    "get_product_catalog",
    "reset_product_catalog_cache",
]
