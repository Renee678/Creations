"""Synthetic prices.

The public H&M dataset has no list prices (the Kaggle transactions only carry a
normalised price), so each product gets a deterministic budget-retail price:
a typical price for its category, varied +/-35% by a hash of the article id.
Deterministic so tests, demos and screenshots are reproducible.
"""

import hashlib

BASE_PRICE_USD = {
    "top": 12.0,
    "bottom": 18.0,
    "dress": 22.0,
    "outerwear": 38.0,
    "shoes": 28.0,
    "bag": 20.0,
    "accessory": 8.0,
}


def synthetic_price(article_id: str, category: str) -> float:
    base = BASE_PRICE_USD.get(category, 15.0)
    h = int.from_bytes(hashlib.sha256(article_id.encode()).digest()[:4], "little") / 2**32
    # Whole-dollar price minus a cent, the way budget retailers label things ($11.99).
    return max(round(base * (0.65 + 0.7 * h)), 2) - 0.01
