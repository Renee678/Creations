from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product
from .embedder import Embedder
from .index import VectorIndex


@dataclass(frozen=True)
class ProductView:
    id: str
    name: str
    product_type: str
    category: str
    colour: str
    description: str
    image_url: str
    price: float


@dataclass(frozen=True)
class SearchResult:
    product: ProductView
    score: float


class Catalog:
    """Read-only catalog held in memory: product metadata plus the vector index."""

    def __init__(self, products: list[ProductView], index: VectorIndex, embedder: Embedder):
        self.products = {p.id: p for p in products}
        self.index = index
        self.embedder = embedder

    @classmethod
    def load(cls, session: Session, embedder: Embedder) -> "Catalog":
        rows = session.scalars(select(Product).order_by(Product.id)).all()
        products = [
            ProductView(r.id, r.name, r.product_type, r.category, r.colour, r.description, r.image_url, r.price)
            for r in rows
        ]
        vectors = (
            np.stack([np.frombuffer(r.embedding, dtype=np.float32) for r in rows])
            if rows else np.zeros((0, embedder.dim), dtype=np.float32)
        )
        index = VectorIndex([p.id for p in products], vectors, [p.category for p in products], [p.price for p in products])
        return cls(products, index, embedder)

    def search(
        self, query: str, k: int = 5, category: str | None = None,
        max_price: float | None = None, exclude: set[str] | None = None,
    ) -> list[SearchResult]:
        hits = self.index.search(self.embedder.embed_query(query), k, category, max_price, exclude)
        return [SearchResult(self.products[h.product_id], h.score) for h in hits]
