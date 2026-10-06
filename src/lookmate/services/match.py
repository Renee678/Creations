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
               ("jeans", ("jean", "denim", "jegging")), ("trousers", ("trouser", "pant", "jogger", "culotte", "chino", "cargo"))],
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

# Length only means something for skirts and dresses. Trousers are full length unless they say otherwise.
LENGTH_SUBTYPES = {"skirt", "dress"}
CROPPED = re.compile(r"\b(cropped|crop|capri|7/8|ankle[- ]grazer|culottes?)\b")
PATTERNED = re.compile(r"\b(print|printed|prints|graphic|slogan|logo|character|disney|mickey|minnie|marvel|cartoon|"
                       r"floral|flower|flowers|stripe|stripes|striped|stripy|check|checked|checks|plaid|tartan|gingham|"
                       r"leopard|animal|zebra|snake|polka|dot|dots|spot|spots|tie[- ]dye|camo|camouflage|paisley|"
                       r"houndstooth|argyle|fair isle|patterned|pattern)\b")
# Descriptions say "spot clean" and "adds character": only unambiguous print words count there.
LOUD_IN_DESCRIPTION = re.compile(r"\b(printed|all-over print|graphic|slogan|floral|striped|leopard|animal print|disney|"
                                 r"mickey|minnie|tie[- ]dye|camo|paisley|plaid|tartan|gingham|polka dot)\b")

LENGTHS = [("mini", ("mini", "micro")), ("midi", ("midi", "knee length", "knee-length", "calf")),
           ("maxi", ("maxi", "floor length", "floor-length", "full length", "ankle length", "long skirt", "long dress"))]
LENGTH_ORDER = ["mini", "midi", "maxi"]

# Leg shape, for trousers and jeans: a wide-leg original never gets skinny or pencil pants, and the other way round.
LEGS = [("narrow", ("skinny", "pencil", "slim", "tapered", "cigarette", "drainpipe", "jegging")),
        ("wide", ("wide", "palazzo", "flare", "flared", "bootcut", "boot cut", "boot-cut", "straight", "barrel",
                  "baggy", "balloon", "culotte", "kick flare", "relaxed leg", "loose"))]
LEG_SUBTYPES = {"trousers", "jeans"}

# A shade word next to the colour: light blue and dark blue are not the same colour.
SHADES = [("dark", ("dark", "deep", "indigo", "midnight", "raw denim", "rinse", "dark wash", "dark-wash", "ink")),
          ("light", ("light", "pale", "baby", "powder", "sky", "ice", "icy", "pastel", "bleach", "bleached", "light wash",
                     "light-wash"))]
# A front opening is what makes a cardigan; a knit "with buttons down the front" is one even when not named so.
FRONT_OPENING = re.compile(r"\b(with (\w+ )?buttons|button[- ]?(front|up|down|through)|buttons? down the front|buttoned|front buttons?|"
                           r"open[- ]front|zip[- ]?(front|up)|opens at the front)\b")

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


def _whole_word(text: str, table) -> str | None:
    """Like _first, but whole words only: "lightweight" is not light, "slimming" is not slim."""
    t = text.lower()
    for name, words in table:
        if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
            return name
    return None


def leg(text: str) -> str | None:
    return _whole_word(text, LEGS)


def shade(text: str) -> str | None:
    return _whole_word(text, SHADES)


def product_kind(product) -> str | None:
    """The garment type from the product's own name; the shop's label only when the name doesn't say.

    ASOS files a cable jumper under "Jumpers & Cardigans": read together, that jumper looked like a cardigan.
    A label naming two types decides nothing.
    """
    kind = subtype(product.category, product.name)
    if kind:
        return kind
    table = SUBTYPES.get(product.category, [])
    label = product.product_type.lower()
    kinds = {name for name, words in table if any(re.search(rf"\b{re.escape(w)}", label) for w in words)}
    return kinds.pop() if len(kinds) == 1 else None


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


def _shop_label_kinds(product) -> set[str]:
    """Garment types in the shop's own section label: ASOS descriptions start "Hoodies & Sweatshirts by ..."."""
    desc = product.description or ""
    label = desc.split(" by ", 1)[0] if " by " in desc[:60] else ""
    table = SUBTYPES.get(product.category, [])
    return {name for name, words in table if any(re.search(rf"\b{re.escape(w)}", label.lower()) for w in words)}


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
        if self.subtype == "knit" and FRONT_OPENING.search(text.lower()):
            self.subtype = "cardigan"  # "crew-neck knit with dark buttons" opens at the front
        self.length = length(text) if self.subtype in LENGTH_SUBTYPES else None
        self.cropped = bool(CROPPED.search(text.lower())) if self.subtype in ("trousers", "jeans") else None
        self.leg = leg(text) if self.subtype in LEG_SUBTYPES else None
        self.colour = colour_family(colour, name)
        self.shade = shade(f"{colour} {name}") if self.colour else None
        self.patterned = bool(PATTERNED.search(text.lower()))

    def check(self, product) -> bool:
        text = f"{product.name} {product.product_type}"
        kind = product_kind(product)
        if self.subtype and kind != self.subtype:
            return False  # a skirt is never a dupe for trousers, however close the fabric
        label = _shop_label_kinds(product)
        if self.subtype and label and self.subtype not in label:
            return False  # named a "jumper" but filed under Hoodies & Sweatshirts
        if self.length and length(text) != self.length:
            return False  # a maxi skirt wants a maxi skirt, not a midi or one of unknown length
        if self.cropped is not None and bool(CROPPED.search(text.lower())) != self.cropped:
            return False  # cropped trousers for cropped, full length (stated or not) for full length
        if self.leg and leg(text) != self.leg:
            return False  # wide-leg trousers want wide or straight legs, stated, never skinny or pencil
        if self.colour and not same_colour(self.colour, colour_family(product.colour, product.name)):
            return False  # a white skirt wants a white skirt
        if not self.same_shade(product):
            return False  # light blue wants light blue, not navy-dark denim
        patterned = PATTERNED.search(text.lower()) or LOUD_IN_DESCRIPTION.search(product.description[:300].lower())
        if bool(patterned) != self.patterned:
            return False  # a plain top never gets a Mickey Mouse sweatshirt, and a floral one wants a print
        return True

    def same_shade(self, product) -> bool:
        """A light colour wants a candidate that says it's light; a dark one never takes a light one."""
        theirs = shade(f"{product.colour} {product.name}")
        if self.shade == "light":
            return theirs == "light"
        return not (self.shade == "dark" and theirs == "light")

    def colour_score(self, product) -> float:
        score = colour_match(self.colour, colour_family(product.colour, product.name))
        return 0.5 if score == 1.0 and not self.same_shade(product) else score
