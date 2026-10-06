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
    sleeve: Literal["sleeveless", "short", "long"] | None = Field(
        default=None, description="Sleeve length as seen in the photo (cap and elbow sleeves are short); null if "
        "the item has no sleeves to judge (trousers, shoes, bags)")
    warmth: Literal["summer", "all-season", "winter"] | None = Field(
        default=None, description="Which weather the piece is made for, judged from the photo: summer (thin jersey, "
        "linen, cap sleeves, straps), winter (chunky, cable or roll-neck knit, wool, fleece, padding) or all-season")
    estimated_original_price_usd: float | None = Field(
        default=None, description="Rough retail price of the item as pictured if it looks designer/premium, else null"
    )
    partial: bool = Field(
        default=False, description="True if less than about half of the item is in frame, e.g. cut off at the edge"
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


class PhotoCheck(BaseModel):
    """What one uploaded photo is good for, so the user knows which photo does what."""

    framing: Literal["face", "upper_body", "full_body", "no_person"] = Field(
        description="How much of the person is visible: face close-up, waist up, head to toe, or nobody")
    good_for_colour: bool = Field(description="Face clearly visible in natural-looking light, no heavy filter")
    good_for_tryon: bool = Field(
        description="One person, standing, facing the camera, visible from head to at least the knees, not cropped")
    tip: str = Field(description="One short sentence: what this photo is good for, or how to retake it")


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
    photo_checks: list[PhotoCheck] = Field(default_factory=list, description="One check per photo, in upload order")


class StylistCandidate(BaseModel):
    id: str
    name: str
    colour: str
    price: float


class StylistSlot(BaseModel):
    slot: str = Field(description="What the slot is for, e.g. 'wide-leg trousers'")
    role: Literal["accent", "neutral"]
    candidates: list[StylistCandidate]


class StylistOutfit(BaseModel):
    index: int
    title: str
    style: str
    style_definition: str
    slots: list[StylistSlot]


class StylingRequest(BaseModel):
    """Outfits to curate: per slot, the top candidates the deterministic scorer already picked."""

    setting: str = Field(description="The season or occasion, e.g. 'autumn' or 'work'")
    palette: list[str]
    avoid: list[str]
    outfits: list[StylistOutfit]


class StyledOutfit(BaseModel):
    index: int = Field(description="The outfit's index from the request")
    picks: list[str] = Field(description="Exactly one candidate id per slot, in slot order")
    approved: bool = Field(description="True only if this outfit looks cohesive and true to its style")
    why: str = Field(description="If approved: one short, casual sentence (12 words or fewer), addressed to the client, on why it works. "
                                 "If not: what clashes")


class StylingResult(BaseModel):
    outfits: list[StyledOutfit]
