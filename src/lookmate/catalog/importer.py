"""Load the product catalog into the database.

CATALOG_SOURCE is one source or a comma-separated mix, e.g. "asos,polyvore":
- "asos":     ASOS products (UniqueData/asos-e-commerce-dataset on Hugging Face, CC BY-NC-ND 4.0):
              affordable pieces with real prices and live product photos.
- "polyvore": designer pieces from Polyvore outfits (Marqo/polyvore, Apache 2.0). The photos are
              embedded in the dataset, so they are saved under data/cache/images and served by the app.
- "hm":       the H&M dataset (Qdrant/hm_ecommerce_products, CC BY 4.0) with precomputed BGE-small
              vectors. Its image bucket is gone, so products show no photo.
- "amazon":   women's clothing, shoes and bags from Amazon Reviews 2023 (McAuley Lab, UCSD; item metadata
              for Clothing_Shoes_and_Jewelry), streamed and filtered, up to AMAZON_MAX_ITEMS pieces with real
              prices and Amazon CDN photos. It takes its own share, on top of CATALOG_SIZE for the rest.
- "seed":     a small bundled JSON catalog so the app runs fully offline (tests, CI).
Each download is cached in data/cache; the mix is sampled down to CATALOG_SIZE items in total.
"""

import ast
import csv
import gzip
import hashlib
import json
import logging
import re
import sys
import zlib
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from ..models import Product
from ..services.colours import colour_word
from .categories import category_for, category_from_text
from .embedder import Embedder
from .pricing import GBP_TO_USD, synthetic_price

log = logging.getLogger(__name__)

HM_URL = (
    "https://huggingface.co/datasets/Qdrant/hm_ecommerce_products/resolve/main/"
    "hm_ecommerce_products_enriched.parquet"
)
HM_COLUMNS = [
    "article_id", "prod_name", "product_type_name", "colour_group_name",
    "graphical_appearance_name", "index_group_name", "section_name",
    "detail_desc", "image_url",
]
# The dataset's BGE-small vectors. Older copies called the column bge_embedding.
HM_VECTOR_COLUMNS = ("dense_embedding", "bge_embedding")
HM_GROUPS = {"Ladieswear", "Divided", "Menswear"}
ASOS_URL = "https://huggingface.co/datasets/UniqueData/asos-e-commerce-dataset/resolve/main/products_asos.csv"
# One of six shards (~420 MB, ~15k items) is plenty for a 5k catalog.
POLYVORE_URL = "https://huggingface.co/datasets/Marqo/polyvore/resolve/main/data/data-00000-of-00006.parquet"
AMAZON_URL = ("https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/"
              "meta_Clothing_Shoes_and_Jewelry.jsonl.gz")
AMAZON_SCAN_LIMIT = 3_000_000  # lines read at most; the file has ~7.2M items, most of them not women's apparel
EMBED_CHUNK = 1000  # rows embedded (and cached on disk) at a time, so a failed import resumes
IMAGE_ROUTE = "/catalog-images"  # served from data/cache/images (see main.py)

# Product id prefixes tell the sources apart; H&M ids are bare numeric article ids.
SEED_ID_PREFIX = "seed-"
ID_PREFIXES = {"seed": SEED_ID_PREFIX, "asos": "asos-", "polyvore": "pv-", "amazon": "amz-"}
SOURCES = ("seed", "hm", "asos", "polyvore", "amazon")


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


def _download(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    tmp = target.with_suffix(".part")
    log.info("downloading %s", url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=300)) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(target)  # atomic: a crashed download never looks complete
    return target


def download_hm(cache_dir: Path) -> Path:
    return _download(HM_URL, cache_dir / "hm_products.parquet")


def download_asos(cache_dir: Path) -> Path:
    return _download(ASOS_URL, cache_dir / "asos_products.csv")


def download_polyvore(cache_dir: Path) -> Path:
    return _download(POLYVORE_URL, cache_dir / "polyvore_00000.parquet")


def load_hm_rows(parquet: Path, size: int) -> list[dict]:
    """Filter and sample on the small metadata columns first, then fetch vectors for the sample only.

    Converting all ~106k rows with their 384-d embeddings to Python objects peaks at 1-2 GB of RAM;
    this way only `size` embeddings are ever materialised.
    """
    import pyarrow.parquet as pq

    available = set(pq.read_schema(parquet).names)
    vector_col = next((c for c in HM_VECTOR_COLUMNS if c in available), None)
    meta = pq.read_table(parquet, columns=HM_COLUMNS).to_pylist()
    keep = []
    for i, r in enumerate(meta):
        if r["index_group_name"] not in HM_GROUPS:
            continue
        category = category_for(r["product_type_name"] or "")
        if not category or not r["detail_desc"]:
            continue
        keep.append((_stable_rank(str(r["article_id"])), i, category))
    keep.sort()  # deterministic sample
    keep = keep[:size]

    indices = [i for _, i, _ in keep]
    vectors = (
        pq.read_table(parquet, columns=[vector_col]).column(0).take(indices).to_pylist()
        if vector_col else [None] * len(indices)  # no precomputed vectors: the embedder computes them
    )
    rows = []
    for (_, i, category), vector in zip(keep, vectors):
        r = meta[i]
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
            "vector": vector,
        })
    return rows


_NAME_COLOUR = re.compile(r"\bin ([a-z][a-z \-]{1,30})$")
_JOINED_WORDS = re.compile(r"(?<=[a-z])(?=[A-Z])")
_PRICE = re.compile(r"\d+(?:\.\d+)?")


def _asos_details(raw: str) -> tuple[str, str]:
    """(label, description) from the ASOS description cell: a Python-literal list of dicts.

    'Product Details' reads like "Coats & Jackets by New LookLow-key layeringNotch collar...":
    the shop category, the brand, then bullet points glued together.
    """
    try:
        parts = ast.literal_eval(raw)
        details = next((d["Product Details"] for d in parts if "Product Details" in d), "")
    except (ValueError, SyntaxError, TypeError):
        details = raw or ""
    details = re.sub(r"Product Code:\s*\d+", "", details)
    label = details.split(" by ", 1)[0] if " by " in details[:60] else ""
    text = _JOINED_WORDS.sub(". ", details).strip()
    return label, text[:600]


def _first_image(raw: str) -> str:
    try:
        urls = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        urls = re.findall(r"https://[^'\",\s\]]+", raw or "")
    url = next((u for u in urls if isinstance(u, str) and u.startswith("https://")), "")
    return url if len(url) <= 400 else url.split("?", 1)[0]


def load_asos_rows(csv_path: Path, size: int) -> list[dict]:
    """ASOS rows repeat per size and colourway; keep one per product, women's fashion only."""
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    products: dict[str, dict] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("name") or "").strip()
            image = _first_image(r.get("images", ""))
            price = _PRICE.search((r.get("price") or "").replace(",", ""))
            if not name or not image or not price:
                continue
            label, description = _asos_details(r.get("description", ""))
            match = category_from_text(label, name)
            if not match:
                continue
            sku = re.sub(r"\D", "", (r.get("sku") or "").split(".")[0])
            key = sku or hashlib.sha256(image.encode()).hexdigest()[:12]
            if key in products:
                continue
            in_colour = _NAME_COLOUR.search(name.lower())
            products[key] = {
                "id": f"asos-{key}",
                "name": name,
                "product_type": label or match[1].capitalize(),
                "category": match[0],
                "colour": in_colour.group(1) if in_colour else (r.get("color") or "").strip(),
                "pattern": "",
                "section": "ASOS",
                "description": description,
                "image_url": image,
                "price": round(float(price.group()) * GBP_TO_USD, 2),
            }
    rows = sorted(products.values(), key=lambda p: _stable_rank(p["id"]))
    return rows[:size]


# Amazon's category path and department say who a piece is for; titles repeat it ("Women's ...").
_AMAZON_NOT_WOMEN = re.compile(r"\b(men|boys|girls|baby|kids|novelty|costumes?|uniforms?|jewelry|watches|luggage|"
                               r"shoe care|accessories > (?:wallets|keyrings))\b", re.I)
_AMAZON_NOISE = re.compile(r"\b(women'?s|womens|for women|ladies|2024|2025|fall|summer|winter|spring|fashion|trendy|"
                           r"casual|cute|sexy|plus size)\b", re.I)


def _amazon_price(value) -> float | None:
    try:
        price = float(str(value).replace("$", "").replace(",", "").split()[0])
    except (TypeError, ValueError, IndexError):
        return None
    return round(price, 2) if 3 <= price <= 2000 else None


def amazon_row(item: dict) -> dict | None:
    """One Amazon item as a catalog row, or None if it isn't women's apparel, shoes or a bag with a price and photo."""
    title = (item.get("title") or "").strip()
    price = _amazon_price(item.get("price"))
    images = item.get("images") or []
    main = next((i for i in images if (i or {}).get("variant") == "MAIN"), images[0] if images else None) or {}
    image = main.get("large") or main.get("hi_res") or ""  # ~500 px: enough for a card and for try-on
    if not title or price is None or not image.startswith("https://"):
        return None
    details = item.get("details") or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            details = {}
    path = [c for c in (item.get("categories") or []) if isinstance(c, str)]
    department = str(details.get("Department", ""))
    women = "Women" in path[:3] or "women" in department.lower() or bool(re.search(r"\bwomen'?s\b", title, re.I))
    if not women or _AMAZON_NOT_WOMEN.search(" > ".join(path[1:])) or _AMAZON_NOT_WOMEN.search(department):
        return None
    leaf = path[-1] if len(path) > 2 else ""
    match = category_from_text(leaf, title)
    if not match or match[0] == "accessory":
        return None
    features = [f for f in (item.get("features") or []) if isinstance(f, str)]
    described = [d for d in (item.get("description") or []) if isinstance(d, str) and d != "Description"]
    colour = str(details.get("Color") or details.get("Colour") or "").strip()
    store = str(item.get("store") or "").strip()
    if store and title.lower().startswith(store.lower()):
        title = title[len(store):]  # the brand stays in `section`; the name says what the piece is
    name = re.sub(r"\s+", " ", _AMAZON_NOISE.sub(" ", title)).strip(" ,-|")[:140]
    return {
        "id": f"amz-{item['parent_asin']}",
        "name": name[:1].upper() + name[1:],
        "product_type": leaf or match[1].capitalize(),
        "category": match[0],
        "colour": colour if colour.lower() not in ("as shown", "multicolor", "multi", "") else colour_word(title),
        "pattern": "",
        "section": f"Amazon · {item.get('store') or 'unbranded'}"[:60],
        "description": " ".join(features + described)[:600],
        "image_url": image,
        "price": price,
    }


def _stream_lines(url: str):
    """Lines of a remote .jsonl.gz, decompressed as they arrive: the file is never stored whole."""
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=300)) as r:
        r.raise_for_status()
        inflate, buffer = zlib.decompressobj(16 + zlib.MAX_WBITS), b""
        for chunk in r.iter_bytes(1 << 20):
            buffer += inflate.decompress(chunk)
            *lines, buffer = buffer.split(b"\n")
            yield from lines
        buffer += inflate.flush()
        if buffer:
            yield buffer


def load_amazon_rows(cache_dir: Path, size: int, lines=None) -> list[dict]:
    """Stream the metadata once, keep up to `size` pieces, and cache them, so a re-import skips the download."""
    cached = cache_dir / f"amazon_rows_{size}.jsonl.gz"
    if cached.exists():
        with gzip.open(cached, "rt", encoding="utf-8") as f:
            return [json.loads(line) for line in f]
    rows, seen = [], set()
    for n, line in enumerate(lines if lines is not None else _stream_lines(AMAZON_URL)):
        if n >= AMAZON_SCAN_LIMIT or len(rows) >= size:
            break
        try:
            row = amazon_row(json.loads(line))
        except (ValueError, KeyError, TypeError):
            continue
        if row and row["id"] not in seen and row["name"].lower() not in seen:
            seen.update((row["id"], row["name"].lower()))
            rows.append(row)
        if n and n % 200_000 == 0:
            log.info("amazon: read %d items, kept %d", n, len(rows))
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.rename(cached)  # atomic: an interrupted scan starts over instead of reading half a list
    return rows


def _image_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


def load_polyvore_rows(parquet: Path, size: int, image_dir: Path) -> list[dict]:
    """Pick designer apparel by Polyvore's category label, then save just those photos to disk."""
    import pyarrow.parquet as pq

    meta = pq.read_table(parquet, columns=["item_ID", "category", "text"]).to_pylist()
    picked, seen = {}, set()
    for r in meta:
        text, label = (r["text"] or "").strip(), (r["category"] or "").strip()
        match = category_from_text(label, text) if text else None
        if match and text.lower() not in seen:  # the same piece appears in many outfits
            seen.add(text.lower())
            picked[r["item_ID"]] = (label, text, match[0])
    keep = sorted(picked, key=lambda i: _stable_rank(f"pv-{i}"))[:size]
    wanted = set(keep)

    image_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for batch in pq.ParquetFile(parquet).iter_batches(batch_size=512, columns=["item_ID", "image"]):
        for item_id, image in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
            if item_id not in wanted or item_id in files or not image or not image.get("bytes"):
                continue
            data = image["bytes"]
            name = f"{re.sub(r'[^A-Za-z0-9_-]', '', item_id)}.{_image_ext(data)}"
            path = image_dir / name
            if not path.exists():  # imports can re-run; photos never change
                path.write_bytes(data)
            files[item_id] = name

    rows = []
    for item_id in keep:
        if item_id not in files:
            continue  # no photo, no product: the point of this source is the pictures
        label, text, category = picked[item_id]
        pid = f"pv-{item_id}"
        rows.append({
            "id": pid,
            "name": text[:1].upper() + text[1:],
            "product_type": label,
            "category": category,
            "colour": colour_word(text),
            "pattern": "",
            "section": "Polyvore designer",
            "description": f"Designer {label.lower()}: {text}.",
            "image_url": f"{IMAGE_ROUTE}/polyvore/{files[item_id]}",
            "price": synthetic_price(pid, category, designer=True),
        })
    return rows


def _embed(rows: list[dict], embedder: Embedder, cache_dir: Path | None) -> np.ndarray:
    """Embed in chunks, each saved on disk: a restart after a crash (or a redeploy) reuses what's done."""
    texts = [product_text(r) for r in rows]
    if cache_dir is None:
        return embedder.embed_documents(texts)
    out = []
    folder = cache_dir / "embeddings"
    folder.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(texts), EMBED_CHUNK):
        chunk = texts[start:start + EMBED_CHUNK]
        key = hashlib.sha256("\0".join([embedder.name, str(embedder.dim), *chunk]).encode()).hexdigest()[:32]
        path = folder / f"{key}.npy"
        if path.exists():
            out.append(np.load(path))
            continue
        vectors = embedder.embed_documents(chunk)
        tmp = folder / f"{key}.part.npy"
        np.save(tmp, vectors)
        tmp.rename(path)
        out.append(vectors)
        log.info("embedded %d/%d products", min(start + EMBED_CHUNK, len(texts)), len(texts))
    return np.concatenate(out) if out else np.zeros((0, embedder.dim), dtype=np.float32)


STORE_CHUNK = 2000  # rows inserted per statement: plain dicts, not ORM objects, to keep a 4 GB box comfortable


def _clip(row: dict) -> dict:
    """Make each text value fit Postgres: cut it to its column's length and drop NUL characters. Postgres rejects
    either and fails the whole import (an Amazon "Color" can run past 60 characters); SQLite checks neither."""
    for col in Product.__table__.columns:
        value = row.get(col.name)
        if isinstance(value, str):
            value = value.replace("\x00", "")
            limit = getattr(col.type, "length", None)
            row[col.name] = value[:limit] if limit else value
    return row


def _store(session: Session, rows: list[dict], embedder: Embedder, cache_dir: Path | None = None) -> int:
    """Replace the stored catalog with these rows. Embedding (the slow part) happens before anything is deleted."""
    precomputed = embedder.name == "bge" and all(r.get("vector") is not None for r in rows)
    if precomputed:
        vectors = np.array([r["vector"] for r in rows], dtype=np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    else:
        vectors = _embed(rows, embedder, cache_dir)
    session.execute(delete(Product))
    for start in range(0, len(rows), STORE_CHUNK):
        session.execute(insert(Product), [_clip({
            "id": r["id"], "name": r["name"], "product_type": r["product_type"], "category": r["category"],
            "colour": r["colour"], "pattern": r["pattern"], "section": r["section"],
            "description": r["description"], "image_url": r["image_url"],
            "price": r.get("price") or synthetic_price(r["id"], r["category"]),
            "embedding": v.astype(np.float32).tobytes(),
        }) for r, v in zip(rows[start:start + STORE_CHUNK], vectors[start:start + STORE_CHUNK])])
    session.commit()
    return len(rows)


def catalog_is_current(session: Session, embedder: Embedder, source: str) -> bool:
    """True when the stored catalog came from exactly these sources with this embedding model."""
    sample = session.scalar(select(Product).limit(1))
    return (sample is not None and len(sample.embedding) // 4 == embedder.dim
            and stored_sources(session) == parse_sources(source))


def ensure_catalog(session: Session, embedder: Embedder, source: str, data_dir: Path, size: int,
                   amazon_size: int = 40_000) -> int:
    """Import the catalog if the table is empty, came from other sources or used a different model.

    The old catalog stays in the table until the new one is fully downloaded and embedded, then both
    are swapped in one transaction: a slow or failed import never leaves the site without products.
    """
    wanted = parse_sources(source)
    count = session.scalar(select(func.count()).select_from(Product)) or 0
    if count:
        if catalog_is_current(session, embedder, source):
            return count
        log.warning("catalog is %s but %s/%s is configured; re-importing",
                    ",".join(sorted(stored_sources(session))), source, embedder.name)

    rows = []
    cache = data_dir / "cache"
    others = wanted - {"amazon"}
    per_source = max(size // max(len(others), 1), 1)
    for name in sorted(wanted - {"seed"}):
        try:
            if name == "amazon":
                rows += load_amazon_rows(cache, amazon_size)
            elif name == "hm":
                rows += load_hm_rows(download_hm(cache), per_source)
            elif name == "asos":
                rows += load_asos_rows(download_asos(cache), per_source)
            elif name == "polyvore":
                rows += load_polyvore_rows(download_polyvore(cache), per_source, cache / "images" / "polyvore")
        except Exception:
            log.exception("%s catalog unavailable; continuing without it", name)
    if "seed" in wanted or not rows:
        if not rows:
            log.warning("no downloadable catalog loaded; using the bundled seed catalog")
        rows += load_seed_rows(data_dir / "seed_products.json")
    n = _store(session, _unique(rows), embedder, cache)
    log.info("imported %d products (source=%s, embedder=%s)", n, source, embedder.name)
    return n


def _unique(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    return [r for r in rows if not (r["id"] in seen or seen.add(r["id"]))]


def parse_sources(source: str) -> set[str]:
    wanted = {s.strip().lower() for s in source.split(",") if s.strip()}
    unknown = wanted - set(SOURCES)
    if unknown or not wanted:
        raise ValueError(f"CATALOG_SOURCE must be a comma-separated mix of {', '.join(SOURCES)}; got {source!r}")
    return wanted


def stored_sources(session: Session) -> set[str]:
    """Which sources the stored catalog came from, read from the product id prefixes."""
    found = set()
    for name, prefix in ID_PREFIXES.items():
        if session.scalar(select(Product.id).where(Product.id.startswith(prefix)).limit(1)):
            found.add(name)
    others = select(Product.id)
    for prefix in ID_PREFIXES.values():
        others = others.where(~Product.id.startswith(prefix))
    if session.scalar(others.limit(1)):
        found.add("hm")
    return found
