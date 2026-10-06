"""Virtual try-on: "try this outfit on me" from the Lookbook."""

import hashlib
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..llm.client import ALLOWED_MEDIA_TYPES, LLMError
from ..models import TryOn
from ..tryon.client import REGIONS, TRYON_STAGE, tryon_quota_key
from ..tryon.garments import BROWSER_PHOTO_MAX_BYTES, fetch_shop_photo, prefetch, store_browser_photo
from ..worker import tryon_job
from .looks import require_access_code
from .profiles import get_user_or_404

router = APIRouter(tags=["try-on"])
log = logging.getLogger(__name__)

MAX_PIECES = 6


def tryon_out(rec: TryOn) -> dict:
    return {
        "id": rec.id, "status": rec.status, "attempts": rec.attempts, "error": rec.error,
        "product_ids": rec.product_ids, "result": rec.result,
        "image_url": f"/api/tryons/{rec.id}/image" if rec.result_image else None,
    }


def take_tryon_quota(redis_client, limit: int) -> bool:
    """Each rendered try-on costs real money on Replicate, so it has its own daily cap."""
    if limit <= 0:
        return True
    key = tryon_quota_key()
    used = redis_client.incr(key)
    if used == 1:
        redis_client.expire(key, 2 * 24 * 3600)
    return used <= limit


@router.get("/api/tryon")
def tryon_info(request: Request) -> dict:
    tryon = request.app.state.runtime.tryon
    return {"renders": tryon.renders, "model": tryon.name}


@router.post("/api/users/{user_id}/photo-check")
async def check_tryon_photo(
    user_id: int,
    request: Request,
    photo: UploadFile = File(...),
    x_access_code: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Is this photo usable for try-on (head to knees, facing the camera)? Checked before it's saved,
    so a waist-up photo is turned away with a tip instead of producing a broken render."""
    settings = get_settings()
    require_access_code(x_access_code)
    get_user_or_404(db, user_id)
    if photo.content_type not in ALLOWED_MEDIA_TYPES - {"image/gif"}:
        raise HTTPException(415, "use a JPEG, PNG or WebP photo")
    data = await photo.read(settings.max_upload_bytes + 1)
    if not data or len(data) > settings.max_upload_bytes:
        raise HTTPException(413 if data else 400, "use a photo under 8 MB")
    try:
        check = await run_in_threadpool(request.app.state.runtime.llm.check_photo, data, photo.content_type)
    except LLMError as e:
        raise HTTPException(503, f"couldn't check the photo: {e}") from e
    return check.model_dump()


@router.post("/api/users/{user_id}/tryons")
async def create_tryon(
    user_id: int,
    request: Request,
    photo: UploadFile | None = File(None),
    product_ids: str = Form(...),
    x_access_code: str = Header(default=""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    rt = request.app.state.runtime
    require_access_code(x_access_code)
    get_user_or_404(db, user_id)

    ids = list(dict.fromkeys(i.strip() for i in product_ids.split(",") if i.strip()))
    if not 1 <= len(ids) <= MAX_PIECES:
        raise HTTPException(400, f"pick 1 to {MAX_PIECES} pieces")
    unknown = [i for i in ids if i not in rt.catalog.products]
    if unknown:
        raise HTTPException(404, f"unknown products: {', '.join(unknown)}")
    if not any(rt.catalog.products[i].category in REGIONS for i in ids):
        raise HTTPException(400, "nothing to try on: pick a top, bottom, dress or jacket")

    # "My model" is the person whenever the user has saved one; a photo is only needed without it.
    from .body_model import saved_model  # imported here: body_model imports this module

    model = saved_model(db, user_id)
    if model is not None:
        data, media_type = model.result_image, model.result_media_type or "image/jpeg"
    elif photo is None:
        raise HTTPException(400, "add a full-body photo, or create My model in Profile")
    else:
        if photo.content_type not in ALLOWED_MEDIA_TYPES - {"image/gif"}:
            raise HTTPException(415, "use a JPEG, PNG or WebP photo")
        data, media_type = await photo.read(settings.max_upload_bytes + 1), photo.content_type
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "image too large (max 8 MB)")
        if not data:
            raise HTTPException(400, "empty image")

    # Idempotent: the same photo and outfit return the existing try-on instead of paying again.
    key = hashlib.sha256(hashlib.sha256(data).digest() + ",".join(ids).encode()).hexdigest()
    existing = db.scalar(select(TryOn).where(TryOn.user_id == user_id, TryOn.request_sha256 == key))
    if existing is not None and existing.status != "failed":
        return JSONResponse(tryon_out(existing) | {"deduplicated": True}, status_code=200)
    if existing is not None:  # a failed try-on is retried, not handed back
        db.delete(existing)
        db.flush()

    if rt.tryon.renders and not take_tryon_quota(rt.redis, settings.daily_tryon_limit):
        raise HTTPException(429, "Today's try-on quota is used up. Please come back tomorrow.")

    rec = TryOn(id=secrets.token_hex(16), user_id=user_id, request_sha256=key, product_ids=ids,
                media_type=media_type, photo=data, from_model=model is not None)
    db.add(rec)
    db.commit()
    rt.tryon_queue.enqueue(tryon_job(rec.id))
    return JSONResponse(tryon_out(rec) | {"deduplicated": False}, status_code=202)


def _get(db: Session, tryon_id: str) -> TryOn:
    rec = db.get(TryOn, tryon_id)
    if rec is None:
        raise HTTPException(404, "try-on not found")
    return rec


@router.get("/api/tryons/{tryon_id}")
def get_tryon(tryon_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    rec = _get(db, tryon_id)
    stage = request.app.state.runtime.redis.get(TRYON_STAGE.format(tryon_id)) if rec.status == "processing" else None
    return tryon_out(rec) | {"stage": stage.decode() if isinstance(stage, bytes) else stage}


class PrefetchIn(BaseModel):
    product_ids: list[str] = Field(max_length=MAX_PIECES * 2)


@router.post("/api/tryon/prefetch", status_code=202)
def prefetch_photos(body: PrefetchIn, request: Request, background: BackgroundTasks) -> dict:
    """A piece went into the fitting room: load its shop photo now, so the try-on doesn't wait for the CDN."""
    rt = request.app.state.runtime
    products = [rt.catalog.products[i] for i in body.product_ids if i in rt.catalog.products]
    if rt.tryon.renders and products:
        background.add_task(prefetch, products, rt.data_dir)
    return {"queued": len(products) if rt.tryon.renders else 0}


@router.get("/api/products/{product_id}/photo")
async def product_photo(product_id: str, request: Request) -> Response:
    """A shop photo from our own origin, so a Look Book page can be drawn into a saved image.

    Browsers refuse to export a canvas holding another site's picture; this serves the catalog photo
    through the same disk cache the try-on uses. Only catalog pieces are served, never arbitrary URLs.
    """
    rt = request.app.state.runtime
    product = rt.catalog.products.get(product_id)
    if product is None or not product.image_url.startswith("https://"):
        raise HTTPException(404, "no shop photo for this piece")
    data, media_type = await run_in_threadpool(fetch_shop_photo, product.image_url, rt.data_dir)
    if data is None:
        raise HTTPException(404, "the shop photo could not be loaded")
    return Response(data, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@router.post("/api/products/{product_id}/photo")
async def upload_product_photo(product_id: str, request: Request, blocked: bool = False,
                               x_access_code: str = Header(default="")) -> dict:
    """The shop photo as the shopper's browser loaded it, when the shop's CDN refuses our server.

    The fitting room fetches each piece's photo in the browser and posts the bytes here; they go into the
    cache the try-on reads, under that piece's photo URL. `blocked=true` (no body) only records that the
    browser couldn't read it either. An upload never replaces a photo already cached.
    """
    rt = request.app.state.runtime
    require_access_code(x_access_code)
    product = rt.catalog.products.get(product_id)
    if product is None or not product.image_url.startswith("https://"):
        raise HTTPException(404, "no shop photo for this piece")
    if blocked:
        log.info("shop photo for %s: browser fetch blocked by CORS: %s", product_id, product.image_url)
        return {"status": "blocked"}
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > BROWSER_PHOTO_MAX_BYTES:
        raise HTTPException(413, "photo too large")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > BROWSER_PHOTO_MAX_BYTES:
            raise HTTPException(413, "photo too large")
    status = await run_in_threadpool(store_browser_photo, product.image_url, rt.data_dir, bytes(data))
    log.info("shop photo for %s from the browser: %s", product_id, status)
    if status == "rejected":
        raise HTTPException(415, "not a usable product photo")
    return {"status": status}


@router.get("/api/tryons/{tryon_id}/image")
def tryon_image(tryon_id: str, db: Session = Depends(get_db)) -> Response:
    rec = _get(db, tryon_id)
    if rec.result_image is None:
        raise HTTPException(404, "no image yet")
    return Response(rec.result_image, media_type=rec.result_media_type or "image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/api/tryons/{tryon_id}", status_code=204)
def delete_tryon(tryon_id: str, db: Session = Depends(get_db)) -> Response:
    db.delete(_get(db, tryon_id))
    db.commit()
    return Response(status_code=204)
