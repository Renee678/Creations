from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
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
