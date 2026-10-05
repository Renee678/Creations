"""The contract between the vision model and the rest of the app.

Everything downstream (search, ranking, style memory) depends only on these
validated types, never on raw model text.
"""

from typing import Literal

from pydantic import BaseModel, Field

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
