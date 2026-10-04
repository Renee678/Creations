"""Load the product catalog into the database.

Two sources:
- "hm":   the public H&M dataset (Qdrant/hm_ecommerce_products on Hugging Face,
          CC BY 4.0, ~106k articles with precomputed BGE-small vectors). Downloaded
          once into data/cache and sampled down to CATALOG_SIZE items.
- "seed": a small bundled JSON catalog so the app runs fully offline (tests, CI).
"""

import hashlib
import json
import logging
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import Product
from .categories import category_for
from .embedder import Embedder
from .pricing import synthetic_price

log = logging.getLogger(__name__)

HM_URL = (
    "https://huggingface.co/datasets/Qdrant/hm_ecommerce_products/resolve/main/"
    "hm_ecommerce_products_enriched.parquet"
)
HM_COLUMNS = [
    "article_id", "prod_name", "product_type_name", "colour_group_name",
    "graphical_appearance_name", "index_group_name", "section_name",
    "detail_desc", "image_url", "bge_embedding",
]
HM_GROUPS = {"Ladieswear", "Divided", "Menswear"}


def product_text(p: dict) -> str:
    """The text a product is embedded from; also used for keyword fallback."""
    parts = [p["name"], p["product_type"], p["colour"], p.get("pattern", ""), p.get("section", ""), p.get("description", "")]
    return ". ".join(x for x in parts if x)


def _stable_rank(article_id: str) -> int:
    return int.from_bytes(hashlib.sha256(article_id.encode()).digest()[:8], "little")


def load_seed_rows(path: Path) -> list[dict]:
    rows = json.loads(path.read_text())
    for r in rows:
        r.setdefault("pattern", "")
        r.setdefault("section", "")
        r.setdefault("image_url", "")
        r["category"] = r.get("category") or category_for(r["product_type"])
    return [r for r in rows if r["category"]]


def download_hm(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "hm_products.parquet"
    if target.exists():
        return target
    tmp = target.with_suffix(".part")
    log.info("downloading H&M catalog (~250MB) from %s", HM_URL)
    with httpx.stream("GET", HM_URL, follow_redirects=True, timeout=httpx.Timeout(30, read=300)) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(target)  # atomic: a crashed download never looks complete
    return target


def load_hm_rows(parquet: Path, size: int) -> list[dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(parquet, columns=HM_COLUMNS)
    rows = []
    for r in table.to_pylist():
        if r["index_group_name"] not in HM_GROUPS:
            continue
        category = category_for(r["product_type_name"] or "")
        if not category or not r["detail_desc"]:
            continue
        rows.append({
            "id": str(r["article_id"]),
            "name": r["prod_name"],
            "product_type": r["product_type_name"],
            "category": category,
            "colour": r["colour_group_name"] or "",
            "pattern": r["graphical_appearance_name"] or "",
            "section": r["section_name"] or "",
            "description": r["detail_desc"],
            "image_url": r["image_url"] or "",
            "vector": r["bge_embedding"],
        })
    rows.sort(key=lambda r: _stable_rank(r["id"]))  # deterministic sample
    return rows[:size]


def _store(session: Session, rows: list[dict], embedder: Embedder) -> int:
    precomputed = embedder.name == "bge" and all(r.get("vector") is not None for r in rows)
    if precomputed:
        vectors = np.array([r["vector"] for r in rows], dtype=np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    else:
        vectors = embedder.embed_documents([product_text(r) for r in rows])
    for r, v in zip(rows, vectors):
        session.add(Product(
            id=r["id"], name=r["name"], product_type=r["product_type"], category=r["category"],
            colour=r["colour"], pattern=r["pattern"], section=r["section"],
            description=r["description"], image_url=r["image_url"],
            price=r.get("price") or synthetic_price(r["id"], r["category"]),
            embedding=v.astype(np.float32).tobytes(),
        ))
    session.commit()
    return len(rows)


def ensure_catalog(session: Session, embedder: Embedder, source: str, data_dir: Path, size: int) -> int:
    """Import the catalog if the table is empty or was embedded with a different model."""
    count = session.scalar(select(func.count()).select_from(Product)) or 0
    if count:
        sample = session.scalar(select(Product.embedding).limit(1))
        if len(sample) // 4 == embedder.dim:
            return count
        log.warning("catalog vectors don't match embedder %s; re-importing", embedder.name)
        session.execute(delete(Product))
        session.commit()

    if source == "hm":
        try:
            rows = load_hm_rows(download_hm(data_dir / "cache"), size)
        except Exception:
            log.exception("H&M catalog unavailable, falling back to the bundled seed catalog")
            rows = load_seed_rows(data_dir / "seed_products.json")
    else:
        rows = load_seed_rows(data_dir / "seed_products.json")
    n = _store(session, rows, embedder)
    log.info("imported %d products (source=%s, embedder=%s)", n, source, embedder.name)
    return n
