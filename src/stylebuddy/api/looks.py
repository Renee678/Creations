import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
    db: Session = Depends(get_db),
):
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
