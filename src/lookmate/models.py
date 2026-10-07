from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    product_type: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40), index=True)  # top/bottom/dress/outerwear/shoes/bag/accessory
    colour: Mapped[str] = mapped_column(String(60))
    pattern: Mapped[str] = mapped_column(String(60), default="")
    section: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(String(400), default="")
    price: Mapped[float] = mapped_column(Float)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nickname: Mapped[str] = mapped_column(String(60))
    height_cm: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[float] = mapped_column(Float)
    age: Mapped[int] = mapped_column(Integer)
    body_shape: Mapped[str] = mapped_column(String(30))
    preferred_styles: Mapped[list] = mapped_column(JSON, default=list)
    budget_per_item: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StyleEvent(Base):
    """One signal for the style memory: an item the user uploaded, saved or rejected."""

    __tablename__ = "style_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # uploaded | saved
    attributes: Mapped[dict] = mapped_column(JSON)  # category, colour, fit, style_tags, ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavedOutfit(Base):
    """A complete outfit the user kept: from the lookbook, Make it mine, or mixed in the fitting room."""

    __tablename__ = "saved_outfits"
    __table_args__ = (UniqueConstraint("user_id", "pieces_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(80))
    product_ids: Mapped[list] = mapped_column(JSON)
    pieces_key: Mapped[str] = mapped_column(String(64))  # sorted ids, hashed: saving the same set twice is a no-op
    season: Mapped[str] = mapped_column(String(10))
    style_id: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(20))  # lookbook | mine | fitting_room
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # For the My Look Book page: the stylist's line, the occasion, and the inspiration look (Make it mine).
    why: Mapped[str | None] = mapped_column(String(300), nullable=True)
    occasion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inspo_look_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Look(Base):
    """One uploaded inspiration photo and, once processed, its look-alike results."""

    __tablename__ = "looks"
    __table_args__ = (UniqueConstraint("user_id", "image_sha256", name="uq_look_user_image"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    image_sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(30))
    # Raw image is only kept until analysis finishes (privacy: we don't store user photos).
    image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Trend(Base):
    __tablename__ = "trends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(40), index=True)  # all trends from one refresh
    style_id: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list] = mapped_column(JSON)
    example_query: Mapped[str] = mapped_column(String(300))
    sources: Mapped[list] = mapped_column(JSON, default=list)
    season: Mapped[str] = mapped_column(String(10), default="autumn")  # spring|summer|autumn|winter
    kind: Mapped[str] = mapped_column(String(20), default="pieces")  # pieces|bags_shoes|beauty|colour
    colours: Mapped[list] = mapped_column(JSON, default=list)  # [{"name": "berry", "hex": "#7d2448"}]
    origin: Mapped[str] = mapped_column(String(20))  # "web" (researched) or "seed" (bundled fallback)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PersonalAnalysis(Base):
    """A colour, face and styling analysis from 1-3 photos of the user; feeds the lookbook."""

    __tablename__ = "personal_analyses"
    __table_args__ = (UniqueConstraint("user_id", "photos_sha256", name="uq_analysis_user_photos"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    photos_sha256: Mapped[str] = mapped_column(String(64))
    # [{"media_type": ..., "data": base64}], kept only until the analysis finishes.
    photos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TryOn(Base):
    """One "try this outfit on me" request: the user's photo dressed in catalog pieces.

    The id is a random token, not a counter, because the result is a picture of the user.
    The uploaded photo is dropped once the job finishes; the result stays until the user deletes it.
    """

    __tablename__ = "tryons"
    __table_args__ = (UniqueConstraint("user_id", "request_sha256", name="uq_tryon_user_request"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_sha256: Mapped[str] = mapped_column(String(64))  # photo + product ids
    product_ids: Mapped[list] = mapped_column(JSON)
    media_type: Mapped[str] = mapped_column(String(30))
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    result_media_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    from_model: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # the person is the user's "My model"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BodyModel(Base):
    """"My model": a standing base figure of the user that the fitting room dresses.

    Made once from one full-body photo (same face, skin tone, hair and real proportions, in plain basics
    on a grey studio background). The uploaded photo is dropped as soon as the job finishes; only the
    saved result is kept, so the model works on every device. The id is a random token: it's a picture
    of the user.
    """

    __tablename__ = "body_models"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    media_type: Mapped[str] = mapped_column(String(30))
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # the upload, until the job ends
    face_photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # optional face close-up, likewise
    face_media_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # generated, note, timings_ms
    result_image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    result_media_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
