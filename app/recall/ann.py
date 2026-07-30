from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class FaissANN:
    """Optional Faiss inner-product index used by the semantic recall channel."""

    def __init__(self, index_path: str | None = None) -> None:
        self.index_path = Path(index_path or os.getenv("ANN_INDEX_PATH", "./data/item_index.faiss"))
        self._index: Any = None

    def load(self) -> None:
        try:
            import faiss  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("install the production extra to use Faiss") from exc
        self._index = faiss.read_index(str(self.index_path))

    def search(self, vector: list[float], top_k: int = 20) -> tuple[list[float], list[int]]:
        if self._index is None:
            self.load()
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("numpy is required by the Faiss adapter") from exc
        scores, ids = self._index.search(np.asarray([vector], dtype="float32"), top_k)
        return scores[0].tolist(), ids[0].tolist()
