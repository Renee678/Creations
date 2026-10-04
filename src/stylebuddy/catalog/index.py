"""In-memory vector index over the catalog.

A brute-force matrix product is exact and takes a few milliseconds for ~10k
products x 384 dims, so an ANN index (pgvector/HNSW, FAISS) would add an extra
moving part without a measurable gain at this size. Revisit beyond ~200k items.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Hit:
    product_id: str
    score: float


class VectorIndex:
    def __init__(self, ids: list[str], vectors: np.ndarray, categories: list[str], prices: list[float]):
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors length mismatch")
        self.ids = np.array(ids)
        self.vectors = vectors.astype(np.float32)
        self.categories = np.array(categories)
        self.prices = np.array(prices, dtype=np.float32)

    def __len__(self) -> int:
        return len(self.ids)

    def search(
        self,
        query: np.ndarray,
        k: int = 5,
        category: str | None = None,
        max_price: float | None = None,
        exclude: set[str] | None = None,
    ) -> list[Hit]:
        if len(self) == 0:
            return []
        scores = self.vectors @ query.astype(np.float32)
        mask = np.ones(len(self), dtype=bool)
        if category:
            mask &= self.categories == category
        if max_price is not None:
            mask &= self.prices <= max_price
        if exclude:
            mask &= ~np.isin(self.ids, list(exclude))
        candidates = np.flatnonzero(mask)
        if candidates.size == 0:
            return []
        k = min(k, candidates.size)
        top = candidates[np.argpartition(-scores[candidates], k - 1)[:k]]
        top = top[np.argsort(-scores[top])]
        return [Hit(str(self.ids[i]), float(scores[i])) for i in top]
