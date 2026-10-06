"""Fetch a catalog piece's photo for the try-on model.

Shop photos are cached on disk, so a piece fetched once (or prefetched when it went into the fitting room)
costs nothing at try-on time. A fetch gets one try within the outfit's budget: a CDN that stalls for longer
usually stalls for minutes. A piece without a photo stops the try-on (it is never drawn from words). Each failure
is logged with its reason (refused, timed out, unreadable format), so `docker compose logs worker` shows why.

When the shop's CDN refuses the server, the shopper's browser can still load the photo: the fitting room fetches
it and hands the bytes over (store_browser_photo), into the same cache.
"""

import hashlib
import io
import logging
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
FETCH_READ_S = 10.0

log = logging.getLogger(__name__)


def _cached(data_dir: Path | None, url: str) -> Path | None:
    if data_dir is None:
        return None
    return data_dir / "cache" / "tryon" / hashlib.sha256(url.encode()).hexdigest()[:32]


def sniff(data: bytes) -> str | None:
    """The image type from the first bytes: a CDN's content-type header isn't always right."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _as_jpeg(data: bytes) -> bytes | None:
    """A photo in a format the try-on models don't read (AVIF, GIF, ...), re-encoded as JPEG."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.load()
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception:  # Pillow raises many types for unreadable images
        return None


def cached_photo(url: str, data_dir: Path | None) -> tuple[bytes, str] | None:
    """The shop photo from the disk cache (the fitting room prefetches it), without touching the network."""
    path = _cached(data_dir, url)
    if path is None or not path.is_file():
        return None
    media_type, _, data = path.read_bytes().partition(b"\n")
    return data, media_type.decode()


def fetch_shop_photo(url: str, data_dir: Path | None, http: httpx.Client | None = None,
                     read_s: float = FETCH_READ_S) -> tuple[bytes | None, str]:
    """(bytes, media type) from the disk cache or the shop CDN; (None, ...) if it can't be had quickly."""
    hit = cached_photo(url, data_dir)
    if hit is not None:
        return hit
    path = _cached(data_dir, url)
    r, why = _get(http or httpx, url, read_s)
    if r is None and _http2_retry_on():
        # Behind Akamai, a CDN can refuse a client by its fingerprint: httpx on HTTP/1.1 doesn't look like Chrome.
        r, why2 = _get(_http2_client(), url, read_s)
        log.warning("shop photo HTTP/2 retry %s: %s", "loaded it" if r is not None else f"failed too ({why2})", url)
    if r is None:
        log.warning("shop photo %s: %s", why, url)
        return None, "image/jpeg"
    data = r.content
    media_type = sniff(data)
    if media_type is None:
        data = _as_jpeg(data)
        if data is None:
            log.warning("shop photo in an unreadable format (%s): %s", r.headers.get("content-type", "?"), url)
            return None, "image/jpeg"
        media_type = "image/jpeg"
    if path is not None:
        _write(path, media_type, data)
    return data, media_type


def _get(client, url: str, read_s: float) -> tuple[httpx.Response | None, str]:
    """(response, "") or (None, why it failed: "refused (403)", "not loaded (ReadTimeout)", ...)."""
    try:
        r = client.get(url, follow_redirects=True, timeout=httpx.Timeout(5, read=read_s), headers=BROWSER_HEADERS)
        r.raise_for_status()
        return r, ""
    except httpx.HTTPStatusError as e:
        return None, f"refused ({e.response.status_code})"
    except httpx.HTTPError as e:
        return None, f"not loaded ({type(e).__name__})"


def _http2_retry_on() -> bool:
    from ..config import get_settings

    return get_settings().shop_fetch_http2


_HTTP2: httpx.Client | None = None


def _http2_client() -> httpx.Client:
    """One shared HTTP/2 client (the h2 package comes with httpx[http2]), for SHOP_FETCH_HTTP2=true."""
    global _HTTP2
    if _HTTP2 is None:
        _HTTP2 = httpx.Client(http2=True)
    return _HTTP2


def _write(path: Path, media_type: str, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_bytes(media_type.encode() + b"\n" + data)
    tmp.replace(path)  # atomic: two fetches of the same photo never leave half a file


BROWSER_PHOTO_MAX_BYTES = 5 * 1024 * 1024
BROWSER_PHOTO_MIN_PX = 100


def store_browser_photo(url: str, data_dir: Path, data: bytes) -> str:
    """Cache a shop photo the shopper's browser loaded, for the piece whose photo URL is `url`.

    Returns "stored", "kept" (the cache already had it: an upload never replaces a photo) or "rejected"
    (too big, not a picture, or too small to be a product photo).
    """
    path = _cached(data_dir, url)
    if path.is_file():
        return "kept"
    if not data or len(data) > BROWSER_PHOTO_MAX_BYTES:
        return "rejected"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if min(img.size) < BROWSER_PHOTO_MIN_PX:
                return "rejected"
            img.verify()
    except Exception:  # Pillow raises many types for unreadable images
        return "rejected"
    media_type = sniff(data)
    if media_type is None:
        data, media_type = _as_jpeg(data), "image/jpeg"
        if data is None:
            return "rejected"
    _write(path, media_type, data)
    return "stored"


def garment_from_words(product: ProductView) -> Garment:
    """A piece without its photo (yet): its name and region, and the URL for providers that load it themselves."""
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
