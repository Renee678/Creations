"""Personal analysis (colour season, face shape, hair and makeup) and the lookbook built from it."""

import base64
import hashlib
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..llm.client import ALLOWED_MEDIA_TYPES
from ..models import Look, PersonalAnalysis
from ..services import stylist
from ..services.lookbook import build_lookbook, latest_analysis
from ..services.make_it_mine import make_it_mine
from ..services.price_range import PriceRange
from ..services.style_memory import user_context
from ..services.trends import SEASONS, latest_batch, season_of
from ..worker import analysis_job
from .looks import require_access_code, take_daily_quota
from .profiles import get_user_or_404

router = APIRouter(tags=["lookbook"])

MAX_PHOTOS = 3


def analysis_out(rec: PersonalAnalysis) -> dict:
    return {
        "id": rec.id, "status": rec.status, "attempts": rec.attempts, "error": rec.error,
        "result": rec.result, "created_at": rec.created_at.isoformat(),
    }


@router.post("/api/users/{user_id}/analyses")
async def upload_photos(
    user_id: int,
    request: Request,
    photos: list[UploadFile] = File(...),
    x_access_code: str = Header(default=""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    require_access_code(x_access_code)
    get_user_or_404(db, user_id)
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise HTTPException(400, f"upload 1 to {MAX_PHOTOS} photos")

    stored, digest = [], hashlib.sha256()
    for photo in photos:
        if photo.content_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(415, f"unsupported image type {photo.content_type}; use JPEG, PNG, WebP or GIF")
        data = await photo.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "image too large (max 8 MB)")
        if not data:
            raise HTTPException(400, "empty image")
        digest.update(hashlib.sha256(data).digest())
        stored.append({"media_type": photo.content_type, "data": base64.b64encode(data).decode()})

    # Idempotent like looks: the same set of photos returns the existing analysis.
    existing = db.scalar(select(PersonalAnalysis).where(
        PersonalAnalysis.user_id == user_id, PersonalAnalysis.photos_sha256 == digest.hexdigest()))
    if existing is not None:
        return JSONResponse(analysis_out(existing) | {"deduplicated": True}, status_code=200)

    if not take_daily_quota(request.app.state.runtime.redis, settings.daily_look_limit):
        raise HTTPException(429, "Today's AI analysis quota is used up. Please come back tomorrow.")

    rec = PersonalAnalysis(user_id=user_id, photos_sha256=digest.hexdigest(), photos=stored)
    db.add(rec)
    db.commit()
    request.app.state.runtime.queue.enqueue(analysis_job(rec.id))
    return JSONResponse(analysis_out(rec) | {"deduplicated": False}, status_code=202)


@router.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db)) -> dict:
    rec = db.get(PersonalAnalysis, analysis_id)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    return analysis_out(rec)


@router.get("/api/users/{user_id}/lookbook/setup")
def lookbook_setup(user_id: int, db: Session = Depends(get_db)) -> dict:
    """What the Lookbook shows before anything is built: the user's palette and the season choices.
    Building outfits waits for "Create my looks", so opening the tab costs no stylist call."""
    get_user_or_404(db, user_id)
    rec = latest_analysis(db, user_id)
    now = season_of(datetime.now(timezone.utc).month)
    return {"analysis": rec.result if rec else None, "personal": rec is not None,
            "current_season": now, "next_season": SEASONS[(SEASONS.index(now) + 1) % 4]}


@router.get("/api/users/{user_id}/lookbook")
def lookbook(
    user_id: int,
    request: Request,
    mode: Literal["seasons", "occasions"] = Query(default="seasons"),
    season: Literal["spring", "summer", "autumn", "winter"] | None = Query(default=None),
    price_min: float | None = Query(default=None, ge=0, le=10000),
    price_max: float | None = Query(default=None, ge=0, le=10000),
    vibe: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
) -> dict:
    rt = request.app.state.runtime
    user = user_context(db, get_user_or_404(db, user_id))
    analysis = latest_analysis(db, user_id)
    # Outfits are labelled with clothing trends only (not a lipstick or a colour), current season first.
    now = season_of(datetime.now(timezone.utc).month)
    upcoming = SEASONS[(SEASONS.index(now) + 1) % 4]
    pieces = sorted((t for t in latest_batch(db) if t.kind == "pieces"), key=lambda t: t.season != now)
    first: dict[str, tuple[str, str]] = {}
    for t in pieces:
        first.setdefault(t.style_id, (t.style_id, t.label))
    trends = list(first.values())
    price = PriceRange.from_params(price_min, price_max, user.budget_per_item)
    # The stylist runs once a photo has been analysed: that's when outfits are personal, and the upload
    # was already gated by the access code, so strangers can't run up model calls.
    curate = stylist_for(rt) if analysis else None
    lb = build_lookbook(rt.catalog, user, analysis, trends, mode, price, season=season or now, stylist=curate,
                        vibe=(vibe or "").strip() or None)
    return {**lb, "current_season": now, "next_season": upcoming, "stylist": rt.llm.name if curate else None}


def stylist_for(rt):
    limit = get_settings().daily_stylist_limit
    return lambda req: stylist.curate(rt.llm, rt.redis, req, limit)


@router.get("/api/looks/{look_id}/mine")
def look_made_mine(
    look_id: int,
    request: Request,
    price_min: float | None = Query(default=None, ge=0, le=10000),
    price_max: float | None = Query(default=None, ge=0, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    """An analysed inspiration look, rebuilt for its owner's colours, body and budget (no new vision call)."""
    rt = request.app.state.runtime
    look = db.get(Look, look_id)
    if look is None:
        raise HTTPException(404, "look not found")
    if look.status != "done":
        raise HTTPException(409, "this look hasn't been analysed yet")
    user = user_context(db, get_user_or_404(db, look.user_id))
    rec = latest_analysis(db, look.user_id)
    price = PriceRange.from_params(price_min, price_max, user.budget_per_item)
    return make_it_mine(look.result, rt.catalog, user, rec.result if rec else None, price,
                        stylist_for(rt) if rec else None)
