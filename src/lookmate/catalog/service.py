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


WHITE_BACKGROUND_SOURCES = ("pv-", "amz-")


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
        links = {
            "shein": f"https://us.shein.com/pdsearch/{quote(self.search_text)}/",
            "asos": f"https://www.asos.com/us/search/?q={quote_plus(self.search_text)}",
        }
        if self.id.startswith("amz-"):  # Amazon listings are long-lived: link the piece itself
            links["amazon"] = f"https://www.amazon.com/dp/{quote(self.id.removeprefix('amz-'))}"
        return links

    @property
    def white_background(self) -> bool:
        """Polyvore and Amazon photos are packshots on white; ASOS shows the piece on a model."""
        return self.id.startswith(WHITE_BACKGROUND_SOURCES)

    def shop(self) -> dict[str, str]:
        """Where the label on a flat lay links: the shop this piece came from (a search there when the
        dataset's product page is long gone; Polyvore closed, so its pieces are searched on SHEIN)."""
        links = self.shop_links()
        if "amazon" in links:
            return {"name": "Amazon", "url": links["amazon"]}
        if self.id.startswith("asos-"):
            return {"name": "ASOS", "url": links["asos"]}
        return {"name": "SHEIN", "url": links["shein"]}

    def to_dict(self) -> dict:
        return asdict(self) | {"shop_links": self.shop_links(), "shop": self.shop(),
                               "white_background": self.white_background}


@dataclass(frozen=True)
class SearchResult:
    product: ProductView
    score: float


def _base_name(p: ProductView) -> str:
    """The piece without its colour: 'ASOS DESIGN midi skirt in black' and '... in red' share it."""
    from ..services.colours import colour_word

    name = re.sub(r"\s+in\s+[a-z][a-z \-]{1,30}$", "", p.name.lower())
    word = colour_word(name)
    if word:
        name = re.sub(rf"\b{re.escape(word)}\b", "", name)
    return f"{p.category}|{re.sub(r'[^a-z0-9]+', ' ', name).strip()}"


class Catalog:
    """Read-only catalog held in memory: product metadata plus the vector index."""

    _variants: dict[str, list[ProductView]] | None = None

    def colour_variants(self, product: ProductView) -> list[ProductView]:
        """The same piece in other colours, when the catalog lists them as separate products."""
        if self._variants is None:
            groups: dict[str, list[ProductView]] = {}
            for p in self.products.values():
                groups.setdefault(_base_name(p), []).append(p)
            self._variants = groups
        return [p for p in self._variants.get(_base_name(product), []) if p.id != product.id]

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
