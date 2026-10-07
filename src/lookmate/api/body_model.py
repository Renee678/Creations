""""My model": a standing base figure of the user, made once and used by every try-on.

Flow: upload one full-body photo -> a queued job draws the same person front-on in plain basics on a grey
studio background -> the user saves it, tries again, or keeps their original photo instead. Only the saved
image is kept, on the server, so it works on every device; the uploaded photo is dropped after the job.
"""

import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..llm.client import ALLOWED_MEDIA_TYPES
from ..models import BodyModel, utcnow
from ..worker import NO_MODEL_NOTE, model_job
from .looks import require_access_code
from .profiles import get_user_or_404
from .tryon import take_tryon_quota

router = APIRouter(tags=["my model"])

ORIGINAL_NOTE = "This is your own photo, used as it is."


def model_out(rec: BodyModel | None) -> dict | None:
    if rec is None:
        return None
    result = rec.result or {}
    return {
        "id": rec.id, "status": rec.status, "attempts": rec.attempts, "error": rec.error, "saved": rec.saved,
        "generated": bool(result.get("generated")), "note": result.get("note"),
        "timings_ms": result.get("timings_ms", {}),
        "image_url": f"/api/body-models/{rec.id}/image" if rec.result_image else None,
    }


def saved_model(db: Session, user_id: int) -> BodyModel | None:
    return db.scalar(select(BodyModel).where(BodyModel.user_id == user_id, BodyModel.saved.is_(True),
                                             BodyModel.result_image.is_not(None)))


@router.post("/api/users/{user_id}/model")
async def create_model(
    user_id: int,
    request: Request,
    photo: UploadFile = File(...),
    face: UploadFile | None = File(None),
    original: bool = Form(False),
    x_access_code: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Start a model from one full-body photo (and optionally a face close-up, for a closer likeness).
    `original=true` keeps the photo as it is instead."""
    settings = get_settings()
    rt = request.app.state.runtime
    require_access_code(x_access_code)
    get_user_or_404(db, user_id)
    data = await _read_photo(photo, settings.max_upload_bytes)
    face_data = await _read_photo(face, settings.max_upload_bytes) if face is not None and face.filename else None

    # Drafts nobody saved go; the saved model stays until a new one is saved over it.
    db.execute(delete(BodyModel).where(BodyModel.user_id == user_id, BodyModel.saved.is_(False),
                                       BodyModel.status.in_(("done", "failed"))))
    rec = BodyModel(id=secrets.token_hex(16), user_id=user_id, media_type=photo.content_type)
    if original or rt.model_maker is None:
        rec.result_image, rec.result_media_type = data, photo.content_type
        rec.status, rec.finished_at = "done", utcnow()
        rec.result = {"generated": False, "note": ORIGINAL_NOTE if original else NO_MODEL_NOTE, "timings_ms": {}}
        db.add(rec)
        db.commit()
        return JSONResponse(model_out(rec), status_code=201)

    if not take_tryon_quota(rt.redis, settings.daily_tryon_limit):
        raise HTTPException(429, "Today's try-on quota is used up. Please come back tomorrow.")
    rec.photo = data
    if face_data:  # optional: a clear close-up of the face, dropped with the photo when the job ends
        rec.face_photo, rec.face_media_type = face_data, face.content_type
    db.add(rec)
    db.commit()
    rt.tryon_queue.enqueue(model_job(rec.id))
    return JSONResponse(model_out(rec), status_code=202)


async def _read_photo(upload: UploadFile, limit: int) -> bytes:
    if upload.content_type not in ALLOWED_MEDIA_TYPES - {"image/gif"}:
        raise HTTPException(415, "use a JPEG, PNG or WebP photo")
    data = await upload.read(limit + 1)
    if not data or len(data) > limit:
        raise HTTPException(413 if data else 400, "use a photo under 8 MB")
    return data


@router.get("/api/users/{user_id}/model")
def get_my_model(user_id: int, db: Session = Depends(get_db)) -> dict:
    get_user_or_404(db, user_id)
    return {"model": model_out(saved_model(db, user_id))}


@router.delete("/api/users/{user_id}/model", status_code=204)
def delete_my_model(user_id: int, db: Session = Depends(get_db)) -> Response:
    get_user_or_404(db, user_id)
    db.execute(delete(BodyModel).where(BodyModel.user_id == user_id))
    db.commit()
    return Response(status_code=204)


def _get(db: Session, model_id: str) -> BodyModel:
    rec = db.get(BodyModel, model_id)
    if rec is None:
        raise HTTPException(404, "model not found")
    return rec


@router.get("/api/body-models/{model_id}")
def get_model(model_id: str, db: Session = Depends(get_db)) -> dict:
    return model_out(_get(db, model_id))


@router.post("/api/body-models/{model_id}/save")
def save_model(model_id: str, db: Session = Depends(get_db)) -> dict:
    """Keep this one as "My model": every other model of the user goes."""
    rec = _get(db, model_id)
    if rec.status != "done" or rec.result_image is None:
        raise HTTPException(409, "this model isn't ready yet")
    db.execute(delete(BodyModel).where(BodyModel.user_id == rec.user_id, BodyModel.id != rec.id))
    rec.saved = True
    db.commit()
    return model_out(rec)


@router.get("/api/body-models/{model_id}/image")
def model_image(model_id: str, db: Session = Depends(get_db)) -> Response:
    rec = _get(db, model_id)
    if rec.result_image is None:
        raise HTTPException(404, "no image yet")
    return Response(rec.result_image, media_type=rec.result_media_type or "image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})
