from pydantic import BaseModel, Field, field_validator

from .services.vocab import BODY_SHAPES, STYLES


class ProfileIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=60)
    height_cm: int = Field(ge=120, le=220)
    weight_kg: float = Field(ge=30, le=200)
    age: int = Field(ge=13, le=100)
    body_shape: str = "unsure"
    preferred_styles: list[str] = Field(default_factory=list, max_length=6)
    budget_per_item: float = Field(default=50, ge=5, le=1000)

    @field_validator("body_shape")
    @classmethod
    def _shape(cls, v: str) -> str:
        if v not in BODY_SHAPES:
            raise ValueError(f"body_shape must be one of {sorted(BODY_SHAPES)}")
        return v

    @field_validator("preferred_styles")
    @classmethod
    def _styles(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(STYLES)
        if unknown:
            raise ValueError(f"unknown styles: {sorted(unknown)}")
        return list(dict.fromkeys(v))


class ProfileOut(ProfileIn):
    id: int
    bmi: float
    fit_advice: str
