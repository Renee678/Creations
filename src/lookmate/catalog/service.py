import re
from dataclasses import asdict, dataclass
from urllib.parse import quote, quote_plus

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product
from .embedder import Embedder
from .index import VectorIndex


# Brands and size ranges at the start of ASOS names. On another shop they only hide the look-alikes:
# SHEIN has no "ASOS DESIGN Petite cropped jumper", but plenty of grey cropped jumpers.
BRANDS = sorted((
    "asos design", "asos edition", "asos luxe", "asos 4505", "asos", "asyou", "& other stories", "other stories",
    "monki", "topshop", "topman", "mango", "weekday", "bershka", "stradivarius", "pull&bear", "pull & bear",
    "collusion", "nike", "adidas originals", "adidas", "puma", "reebok", "new balance", "converse", "vans",
    "vero moda", "pieces", "noisy may", "only", "jdy", "y.a.s", "yas", "object", "selected femme", "vila",
    "miss selfridge", "river island", "new look", "reclaimed vintage", "glamorous", "missguided", "pretty lavish",
    "threadbare", "lipsy", "whistles", "free people", "levi's", "dr martens", "calvin klein jeans", "calvin klein",
    "tommy hilfiger", "tommy jeans", "barbour", "the north face", "columbia", "hollister", "abercrombie & fitch",
    "daisy street", "in the style", "never fully dressed", "wednesday's girl", "urban revivo", "urban threads",
    "forever new", "french connection", "ted baker", "karen millen", "nobody's child", "mama.licious",
    "mamalicious", "public desire", "raid", "truffle collection", "simmi", "steve madden", "ugg", "crocs",
    "skechers", "fila", "champion", "ellesse", "the couture club", "kaiia", "lioness", "na-kd", "nakd",
    "4th & reckless", "fashionkilla", "jaded london", "jaded rose", "only petite", "brave soul", "parisian",
    "qed london", "liquorish", "little mistress", "chi chi london", "trendyol", "cotton:on", "cotton on",
), key=len, reverse=True)
RANGES = {"petite", "tall", "curve", "plus", "maternity", "exclusive", "x", "design"}


def shop_query(name: str, colour: str = "") -> str:
    """What a shopper would type on any site: the piece and its colour, without the brand.

    'ASOS DESIGN Petite cropped jumper in mini cable stitch in grey marl' -> 'grey marl cropped jumper mini cable stitch'.
    """
    text = name.strip()
    while True:
        low = text.lower()
        brand = next((b for b in BRANDS if low.startswith(b) and not low[len(b):len(b) + 1].isalnum()), None)
        first = text.split(" ", 1)[0]
        if brand:
            text = text[len(brand):].strip(" -:|")
        elif first.lower() in RANGES or (len(first) > 1 and first.isupper() and first.isalpha() and " " in text):
            text = text.split(" ", 1)[1]  # 'Petite', or an all-caps brand we don't list
        else:
            break
    text = text.lower()
    head, sep, tail = text.rpartition(" in ")
    if sep and head and len(tail.split()) <= 3:  # 'jumper in grey marl' -> 'grey marl jumper'
        text = f"{tail} {head}"
    text = re.sub(r"\b(in|with)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    colour = re.sub(r".*\bin ", "", colour.lower()).strip()  # 'fluffy yarn in grey' -> 'grey'
    return text if not colour or any(w in text.split() for w in colour.split()) else f"{colour} {text}"


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

    @property
    def search_text(self) -> str:
        """What a shopper would type to find this piece today, e.g. 'beige quilted puffer jacket'."""
        return shop_query(self.name, self.colour)

    def shop_links(self) -> dict[str, str]:
        # Search links, not product pages: the datasets are snapshots, so most product pages are gone.
        return {
            "shein": f"https://us.shein.com/pdsearch/{quote(self.search_text)}/",
            "asos": f"https://www.asos.com/us/search/?q={quote_plus(self.search_text)}",
        }

    def to_dict(self) -> dict:
        return asdict(self) | {"shop_links": self.shop_links()}


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
        max_price: float | None = None, exclude: set[str] | None = None, min_price: float | None = None,
    ) -> list[SearchResult]:
        hits = self.index.search(self.embedder.embed_query(query), k, category, max_price, exclude, min_price)
        return [SearchResult(self.products[h.product_id], h.score) for h in hits]
