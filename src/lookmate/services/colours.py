"""Colour families: a shared language between the model's palette and catalog colour names.

The model says "dusty rose" or "camel"; the H&M catalog says "Light Pink" or "Dark Beige".
Both are mapped onto a small set of families so palette matching is a set lookup.
"""

import re

# Checked in order, so more specific words come first ("dark blue" before "blue").
_FAMILY_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("navy", ("navy", "dark blue", "midnight")),
    ("cream", ("off white", "off-white", "cream", "ivory", "ecru", "soft white", "warm ivory", "vanilla")),
    ("beige", ("greige", "beige", "camel", "tan", "sand", "nude", "khaki", "taupe", "oatmeal", "stone")),
    ("brown", ("brown", "chocolate", "cognac", "coffee", "mocha", "espresso", "chestnut")),
    ("grey", ("grey", "gray", "charcoal", "silver", "slate", "pewter")),
    ("black", ("black", "jet")),
    ("white", ("white", "snow")),
    ("pink", ("pink", "rose", "blush", "fuchsia", "magenta", "mauve", "coral", "salmon")),
    ("red", ("red", "burgundy", "wine", "maroon", "cherry", "crimson", "berry", "raspberry")),
    ("orange", ("orange", "rust", "terracotta", "peach", "apricot", "copper", "pumpkin")),
    ("yellow", ("yellow", "mustard", "butter", "lemon", "gold", "ochre")),
    ("green", ("green", "olive", "sage", "emerald", "mint", "forest", "teal", "jade", "turquoise")),
    ("blue", ("blue", "aqua", "cobalt", "denim", "sky", "cerulean")),
    ("purple", ("purple", "lavender", "lilac", "violet", "plum", "aubergine", "orchid")),
]

NEUTRALS = {"navy", "cream", "beige", "brown", "grey", "black", "white"}


def family(colour: str) -> str | None:
    c = colour.lower().replace("_", " ")
    for fam, words in _FAMILY_WORDS:
        if any(re.search(rf"\b{re.escape(w)}(ish)?\b", c) for w in words):
            return fam
    return None


def families(colours: list[str]) -> set[str]:
    return {f for f in map(family, colours) if f}


def palette_score(product_colour: str, good: set[str], bad: set[str]) -> float:
    """+1 if the product's colour family is in the palette, -1 if it is one to avoid, else 0."""
    f = family(product_colour)
    if f in good:
        return 1.0
    if f in bad:
        return -1.0
    return 0.0


def colour_word(text: str) -> str:
    """The first colour word in free text ("givenchy leather duffel black" -> "black"), or ""."""
    t = text.lower()
    best = None
    for _, words in _FAMILY_WORDS:
        for w in words:
            m = re.search(rf"\b{re.escape(w)}\b", t)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), w)
    return best[1] if best else ""
