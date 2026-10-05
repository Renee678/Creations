"""Fetch a catalog piece's photo for the try-on model."""

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


def garment_for(product: ProductView, data_dir: Path, http: httpx.Client | None = None) -> Garment:
    url = product.image_url
    if url.startswith(IMAGE_ROUTE + "/"):  # saved from a dataset (Polyvore)
        path = data_dir / "cache" / "images" / url.removeprefix(IMAGE_ROUTE + "/")
        if not path.is_file():
            raise TryOnError(f"photo missing for {product.name}")
        data, media_type = path.read_bytes(), _TYPES.get(path.suffix.lstrip(".").lower(), "image/jpeg")
    elif url.startswith("https://"):
        # Shop CDNs often stall requests that don't look like a browser, and sometimes just stall. Try twice;
        # if both fail, the garment carries only its URL and the provider decides what to do with it.
        data, media_type = None, "image/jpeg"
        for read_s in (15, 30):
            try:
                r = (http or httpx).get(url, follow_redirects=True, timeout=httpx.Timeout(10, read=read_s),
                                        headers=BROWSER_HEADERS)
                r.raise_for_status()
                if r.headers.get("content-type", "").split(";")[0] not in MODEL_TYPES:
                    raise httpx.HTTPError("not an image the try-on model reads")
                data, media_type = r.content, r.headers["content-type"].split(";")[0]
                break
            except httpx.HTTPError:
                continue
    else:
        raise TryOnError(f"{product.name} has no photo to try on")
    colour = "" if product.colour.lower() in product.name.lower() else f"{product.colour} "
    return Garment(data, media_type, REGIONS.get(product.category, "accessory"), f"{colour}{product.name}".strip()[:120],
                   url=url if url.startswith("https://") else None)
