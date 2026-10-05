"""Fetch a catalog piece's photo for the try-on model.

Shop photos are cached on disk, so a piece fetched once (or prefetched when it went into the fitting room)
costs nothing at try-on time. A fetch gets one short try: a CDN that stalls for longer usually stalls for
minutes, and the piece is better described in words than waited for.
"""

import hashlib
import secrets
from pathlib import Path

import httpx

from ..catalog.importer import IMAGE_ROUTE
from ..catalog.service import ProductView
from .client import Garment, TryOnError, REGIONS

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    # No AVIF: shop CDNs serve it when asked, and the try-on models don't read it.
    "Accept": "image/jpeg,image/png;q=0.9,image/webp;q=0.8",
    "Referer": "https://www.asos.com/",
}
_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MODEL_TYPES = set(_TYPES.values())
FETCH_READ_S = 6.0


def _cached(data_dir: Path | None, url: str) -> Path | None:
    if data_dir is None:
        return None
    return data_dir / "cache" / "tryon" / hashlib.sha256(url.encode()).hexdigest()[:32]


def fetch_shop_photo(url: str, data_dir: Path | None, http: httpx.Client | None = None,
                     read_s: float = FETCH_READ_S) -> tuple[bytes | None, str]:
    """(bytes, media type) from the disk cache or the shop CDN; (None, ...) if it can't be had quickly."""
    path = _cached(data_dir, url)
    if path is not None and path.is_file():
        media_type, _, data = path.read_bytes().partition(b"\n")
        return data, media_type.decode()
    try:
        r = (http or httpx).get(url, follow_redirects=True, timeout=httpx.Timeout(5, read=read_s),
                                headers=BROWSER_HEADERS)
        r.raise_for_status()
    except httpx.HTTPError:
        return None, "image/jpeg"
    media_type = r.headers.get("content-type", "").split(";")[0]
    if media_type not in MODEL_TYPES:
        return None, "image/jpeg"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(media_type.encode() + b"\n" + r.content)
        tmp.replace(path)  # atomic: two fetches of the same photo never leave half a file
    return r.content, media_type


def garment_from_words(product: ProductView) -> Garment:
    """A piece whose photo didn't arrive in time: the model draws it from its description."""
    colour = "" if product.colour.lower() in product.name.lower() else f"{product.colour} "
    url = product.image_url if product.image_url.startswith("https://") else None
    return Garment(None, "image/jpeg", REGIONS.get(product.category, "accessory"),
                   f"{colour}{product.name}".strip()[:120], url=url)


def prefetch(products: list[ProductView], data_dir: Path, http: httpx.Client | None = None) -> None:
    """Warm the disk cache for shop photos, so a later try-on doesn't wait on the CDN."""
    for p in products:
        if p.image_url.startswith("https://"):
            fetch_shop_photo(p.image_url, data_dir, http, read_s=20)


def garment_for(product: ProductView, data_dir: Path | None, http: httpx.Client | None = None) -> Garment:
    url = product.image_url
    if url.startswith(IMAGE_ROUTE + "/"):  # saved from a dataset (Polyvore)
        path = data_dir / "cache" / "images" / url.removeprefix(IMAGE_ROUTE + "/")
        if not path.is_file():
            raise TryOnError(f"photo missing for {product.name}")
        data, media_type = path.read_bytes(), _TYPES.get(path.suffix.lstrip(".").lower(), "image/jpeg")
    elif url.startswith("https://"):
        # Not fetched in time: the garment carries only its URL and the provider decides what to do with it.
        data, media_type = fetch_shop_photo(url, data_dir, http)
    else:
        raise TryOnError(f"{product.name} has no photo to try on")
    words = garment_from_words(product)
    return Garment(data, media_type, words.region, words.description, url=words.url)
