"""The contract between the vision model and the rest of the app.

Everything downstream (search, ranking, style memory, lookbooks) depends only on these
validated types, never on raw model text.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["top", "bottom", "dress", "outerwear", "shoes", "bag", "accessory"]


class DetectedItem(BaseModel):
    category: Category
    name: str = Field(description="Short English garment name, e.g. 'cropped V-neck cardigan'")
    colour: str = Field(description="Main colour in plain English, e.g. 'beige'")
    fit: str = Field(description="Silhouette/fit in English, e.g. 'relaxed', 'fitted', 'wide-leg'")
    details: list[str] = Field(description="Distinguishing details in English: neckline, sleeves, fabric, pattern")
    style_tags: list[str] = Field(description="Style labels from the allowed vocabulary")
    search_query: str = Field(description="One English sentence describing the item for a product search engine")
    estimated_original_price_usd: float | None = Field(
        default=None, description="Rough retail price of the item as pictured if it looks designer/premium, else null"
    )


class LookAnalysis(BaseModel):
    is_outfit: bool = Field(description="False if the image does not show clothing")
    vibe: str = Field(description="One short sentence describing the overall look")
    style_tags: list[str] = Field(description="Overall style labels from the allowed vocabulary")
    items: list[DetectedItem] = Field(description="Each visible garment, shoe, bag or accessory; at most 6")


ColourSeason = Literal["spring", "summer", "autumn", "winter"]
FaceShape = Literal["oval", "round", "square", "heart", "oblong", "diamond"]


class Swatch(BaseModel):
    name: str = Field(description="Plain English colour name a shop would use, e.g. 'dusty rose', 'navy', 'camel'")
    hex: str = Field(description="Approximate colour as #RRGGBB")

    @field_validator("hex")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("#"):
            v = "#" + v
        return v.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else "#999999"


class PersonAnalysis(BaseModel):
    """Personal colour, face and styling read from 1-3 photos of the user."""

    usable: bool = Field(description="False if no person's face is clearly visible in any photo")
    colour_season: ColourSeason = Field(description="Seasonal colour analysis result")
    season_detail: str = Field(description="Sub-season, e.g. 'soft summer' or 'deep winter'")
    undertone: Literal["warm", "cool", "neutral"]
    contrast: Literal["low", "medium", "high"] = Field(description="Contrast between skin, hair and eyes")
    colouring_notes: str = Field(description="One sentence on what in the photos led to this season")
    best_colours: list[Swatch] = Field(description="8 flattering clothing colours")
    avoid_colours: list[Swatch] = Field(description="4 colours to keep away from the face")
    metals: Literal["gold", "silver", "both"]
    face_shape: FaceShape
    hair_now: str = Field(description="Short description of the current hairstyle and colour")
    hair_suggestions: list[str] = Field(description="3 hairstyle or hair colour suggestions, one sentence each")
    makeup_suggestions: list[str] = Field(description="4 makeup suggestions: base, cheeks, eyes, lips")
    silhouette_notes: str | None = Field(
        default=None, description="Only with a full-body photo: neutral advice on proportions and cuts, else null"
    )
    style_tags: list[str] = Field(description="Style labels from the allowed vocabulary that suit this person")
    caveats: str = Field(description="Anything that limits accuracy, e.g. warm indoor lighting or a beauty filter")
