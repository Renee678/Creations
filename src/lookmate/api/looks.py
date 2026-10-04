import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..llm.client import ALLOWED_MEDIA_TYPES
from ..models import Look
from .profiles import get_user_or_404

router = APIRouter(prefix="/api/looks", tags=["looks"])


def look_out(look: Look) -> dict:
    return {
        "id": look.id, "status": look.status, "attempts": look.attempts, "error": look.error,
        "result": look.result, "created_at": look.created_at.isoformat(),
    }


@router.post("")
async def upload_look(
    request: Request,
    user_id: int = Form(...),
    image: UploadFile = File(...),
    x_access_code: str = Header(default=""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if settings.access_code and not hmac.compare_digest(x_access_code, settings.access_code):
        raise HTTPException(401, "需要体验码")
    get_user_or_404(db, user_id)
    if image.content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(415, f"unsupported image type {image.content_type}; use JPEG, PNG, WebP or GIF")
    data = await image.read(get_settings().max_upload_bytes + 1)
    if len(data) > get_settings().max_upload_bytes:
        raise HTTPException(413, "image too large (max 8 MB)")
    if not data:
        raise HTTPException(400, "empty image")

    # Idempotency: the same user uploading the same photo gets the existing job back,
    # so retries and double-clicks never pay for a second LLM call.
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(Look).where(Look.user_id == user_id, Look.image_sha256 == digest))
    if existing is not None:
        return JSONResponse(look_out(existing) | {"deduplicated": True}, status_code=200)

    if not take_daily_quota(request.app.state.runtime.redis, settings.daily_look_limit):
        raise HTTPException(429, "今天的 AI 识图额度已用完，明天再来吧。")

    look = Look(user_id=user_id, image_sha256=digest, media_type=image.content_type, image=data)
    db.add(look)
    db.commit()
    request.app.state.runtime.queue.enqueue(look.id)
    return JSONResponse(look_out(look) | {"deduplicated": False}, status_code=202)


@router.get("/{look_id}")
def get_look(look_id: int, db: Session = Depends(get_db)) -> dict:
    look = db.get(Look, look_id)
    if look is None:
        raise HTTPException(404, "look not found")
    return look_out(look)


def take_daily_quota(redis_client, limit: int) -> bool:
    """Global daily cap on paid LLM analyses: a cost circuit-breaker for a public demo."""
    if limit <= 0:
        return True
    key = f"quota:looks:{datetime.now(timezone.utc):%Y-%m-%d}"
    used = redis_client.incr(key)
    if used == 1:
        redis_client.expire(key, 2 * 24 * 3600)
    return used <= limit
