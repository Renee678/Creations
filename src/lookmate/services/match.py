"""How close a catalog piece is to the original: garment type, colour and length, as hard rules.

Vector similarity alone let a black satin skirt, a pink mini and black satin trousers stand in for an
ivory satin maxi skirt: "satin" outweighed everything else. Find dupes now only shows a candidate of the
same garment type, colour and length, with no fallback: Renee chose accuracy over a full page, so when
the catalog has no white skirt the user is told so instead of being shown a black one. Each rule is a
word lookup, so it can be unit-tested. When the original states a colour or length, a candidate that
doesn't say its own is skipped rather than guessed.
"""

import re

from .colours import colour_word, family

# Checked in order: the first match wins, so "denim skirt" is a skirt and "t-shirt" is a tee, not a shirt.
SUBTYPES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "bottom": [("skirt", ("skirt", "skort")), ("shorts", ("short",)), ("leggings", ("legging",)),
               ("jeans", ("jean",)), ("trousers", ("trouser", "pant", "jogger", "culotte", "chino", "cargo"))],
    "dress": [("jumpsuit", ("jumpsuit", "playsuit", "romper", "dungaree")), ("dress", ("dress", "gown"))],
    "top": [("cardigan", ("cardigan",)), ("hoodie", ("hoodie", "sweatshirt")), ("tee", ("t-shirt", "tee")),
            ("tank", ("tank", "cami", "camisole", "vest")), ("bodysuit", ("bodysuit",)), ("corset", ("corset", "bustier")),
            ("knit", ("sweater", "jumper", "knit", "pullover", "turtleneck")), ("shirt", ("shirt", "blouse"))],
    "outerwear": [("blazer", ("blazer",)), ("trench", ("trench",)), ("gilet", ("gilet",)),
                  ("coat", ("coat", "parka")), ("jacket", ("jacket", "bomber", "puffer", "shacket"))],
    "shoes": [("boots", ("boot",)), ("sneakers", ("sneaker", "trainer")), ("sandals", ("sandal", "slide", "flip flop")),
              ("heels", ("heel", "pump", "court", "stiletto", "slingback")),
              ("flats", ("flat", "ballet", "ballerina", "loafer", "mary jane", "moccasin", "mule"))],
    "bag": [("backpack", ("backpack",)), ("clutch", ("clutch",)), ("tote", ("tote",)),
            ("crossbody", ("cross-body", "crossbody", "cross body")), ("shoulder", ("shoulder", "hobo", "baguette"))],
}

LENGTHS = [("mini", ("mini", "micro")), ("midi", ("midi", "knee length", "knee-length", "calf")),
           ("maxi", ("maxi", "floor length", "floor-length", "full length", "ankle length", "long skirt", "long dress"))]
LENGTH_ORDER = ["mini", "midi", "maxi"]

LIGHT = {"white", "cream", "beige"}
SAME_COLOUR = [{"white", "cream"}]  # off-white and ivory read as white; beige or grey do not
DARK = {"black", "navy", "grey", "brown"}


def _first(text: str, table) -> str | None:
    t = text.lower()
    for name, words in table:
        if any(re.search(rf"\b{re.escape(w)}", t) for w in words):
            return name
    return None


def subtype(category: str, text: str) -> str | None:
    return _first(text, SUBTYPES.get(category, []))


def length(text: str) -> str | None:
    return _first(text, LENGTHS)


def colour_family(colour: str, name: str = "") -> str | None:
    return family(colour) if colour and family(colour) else family(colour_word(name))


def colour_match(a: str | None, b: str | None) -> float:
    """1 for the same colour family, 0.5 for the same side of the neutrals (ivory ~ white), else 0."""
    if a is None or b is None:
        return 0.5  # unknown: neither rewarded nor ruled out
    if a == b:
        return 1.0
    if (a in LIGHT and b in LIGHT) or (a in DARK and b in DARK):
        return 0.5
    return 0.0


def same_colour(a: str, b: str | None) -> bool:
    return b is not None and (a == b or any(a in pair and b in pair for pair in SAME_COLOUR))


def length_gap(a: str | None, b: str | None) -> int:
    """0 when the same or unknown, 1 for neighbours (midi/maxi), 2 for mini against maxi."""
    if a is None or b is None:
        return 0
    return abs(LENGTH_ORDER.index(a) - LENGTH_ORDER.index(b))


class Target:
    """What a candidate must match, read once from the detected item."""

    def __init__(self, category: str, name: str, colour: str, details: list[str] | None = None, fit: str = ""):
        text = " ".join([name, fit, *(details or [])])
        self.category = category
        self.subtype = subtype(category, name) or subtype(category, text)
        self.length = length(text) if category in ("bottom", "dress") else None
        self.colour = colour_family(colour, name)

    def check(self, product) -> bool:
        text = f"{product.name} {product.product_type}"
        kind = subtype(product.category, text)
        if self.subtype and kind != self.subtype:
            return False  # a skirt is never a dupe for trousers, however close the fabric
        if self.length and length(text) != self.length:
            return False  # a maxi skirt wants a maxi skirt, not a midi or one of unknown length
        if self.colour and not same_colour(self.colour, colour_family(product.colour, product.name)):
            return False  # a white skirt wants a white skirt
        return True

    def colour_score(self, product) -> float:
        return colour_match(self.colour, colour_family(product.colour, product.name))
