"""Synthetic prices.

The public H&M dataset has no list prices (the Kaggle transactions only carry a
normalised price), so each product gets a deterministic budget-retail price:
a typical price for its category, varied +/-35% by a hash of the article id.
Deterministic so tests, demos and screenshots are reproducible.

Datasets that do carry prices (ASOS) use their own. Polyvore has designer pieces without
prices, so those get the same formula scaled into a designer band.
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


DESIGNER_MULTIPLIER = 8
GBP_TO_USD = 1.27  # fixed rate so imported ASOS prices stay reproducible


def synthetic_price(article_id: str, category: str, designer: bool = False) -> float:
    base = BASE_PRICE_USD.get(category, 15.0)
    h = int.from_bytes(hashlib.sha256(article_id.encode()).digest()[:4], "little") / 2**32
    if designer:  # designer labels price in round tens ($290, not $289.99)
        return float(max(round(base * DESIGNER_MULTIPLIER * (0.65 + 0.7 * h), -1), 40))
    # Whole-dollar price minus a cent, the way budget retailers label things ($11.99).
    return max(round(base * (0.65 + 0.7 * h)), 2) - 0.01
