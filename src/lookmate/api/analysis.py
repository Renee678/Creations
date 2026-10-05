"""Personal analysis (colour season, face shape, hair and makeup) and the lookbook built from it."""

import base64
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..llm.client import ALLOWED_MEDIA_TYPES
from ..models import PersonalAnalysis
from ..services.lookbook import build_lookbook, latest_analysis
from ..services.style_memory import user_context
from ..services.trends import latest_batch
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


@router.get("/api/users/{user_id}/lookbook")
def lookbook(
    user_id: int,
    request: Request,
    mode: Literal["seasons", "occasions"] = Query(default="seasons"),
    db: Session = Depends(get_db),
) -> dict:
    user = get_user_or_404(db, user_id)
    analysis = latest_analysis(db, user_id)
    trends = [(t.style_id, t.label) for t in latest_batch(db)]
    return build_lookbook(request.app.state.runtime.catalog, user_context(db, user), analysis, trends, mode)
