"""Fetch a catalog piece's photo for the try-on model."""

from pathlib import Path

import httpx

from ..catalog.importer import IMAGE_ROUTE
from ..catalog.service import ProductView
from .client import Garment, TryOnError, REGIONS

_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def garment_for(product: ProductView, data_dir: Path, http: httpx.Client | None = None) -> Garment:
    url = product.image_url
    if url.startswith(IMAGE_ROUTE + "/"):  # saved from a dataset (Polyvore)
        path = data_dir / "cache" / "images" / url.removeprefix(IMAGE_ROUTE + "/")
        if not path.is_file():
            raise TryOnError(f"photo missing for {product.name}")
        data, media_type = path.read_bytes(), _TYPES.get(path.suffix.lstrip(".").lower(), "image/jpeg")
    elif url.startswith("https://"):
        try:
            r = (http or httpx).get(url, follow_redirects=True, timeout=30,
                                    headers={"User-Agent": "Mozilla/5.0 (Lookmate try-on)"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise TryOnError(f"couldn't fetch the photo of {product.name}: {e}", retryable=True) from e
        data, media_type = r.content, r.headers.get("content-type", "image/jpeg").split(";")[0]
    else:
        raise TryOnError(f"{product.name} has no photo to try on")
    colour = "" if product.colour.lower() in product.name.lower() else f"{product.colour} "
    return Garment(data, media_type, REGIONS[product.category], f"{colour}{product.name}".strip()[:120])
