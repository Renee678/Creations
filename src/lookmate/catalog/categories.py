"""Map catalog product types onto the small set of categories the app reasons about."""

import re

CATEGORIES = ("top", "bottom", "dress", "outerwear", "shoes", "bag", "accessory")

_TYPE_TO_CATEGORY = {
    # tops
    "t-shirt": "top", "top": "top", "vest top": "top", "blouse": "top", "shirt": "top",
    "sweater": "top", "hoodie": "top", "polo shirt": "top", "cardigan": "top", "bodysuit": "top",
    # bottoms
    "trousers": "bottom", "shorts": "bottom", "skirt": "bottom", "leggings/tights": "bottom",
    "outdoor trousers": "bottom",
    # one-pieces
    "dress": "dress", "jumpsuit/playsuit": "dress", "dungarees": "dress",
    # outerwear
    "jacket": "outerwear", "coat": "outerwear", "blazer": "outerwear", "outdoor waistcoat": "outerwear",
    "tailored waistcoat": "outerwear",
    # shoes
    "sneakers": "shoes", "boots": "shoes", "sandals": "shoes", "ballerinas": "shoes", "pumps": "shoes",
    "heeled sandals": "shoes", "flat shoe": "shoes", "other shoe": "shoes", "slippers": "shoes",
    "flip flop": "shoes", "wedge": "shoes", "heels": "shoes", "moccasins": "shoes",
    # bags
    "bag": "bag", "backpack": "bag", "cross-body bag": "bag", "shoulder bag": "bag", "tote bag": "bag",
    "weekend/gym bag": "bag", "bumbag": "bag",
    # accessories
    "belt": "accessory", "hat/beanie": "accessory", "cap/peaked": "accessory", "scarf": "accessory",
    "earring": "accessory", "necklace": "accessory", "sunglasses": "accessory", "hair clip": "accessory",
    "bracelet": "accessory", "ring": "accessory", "hat/brim": "accessory", "gloves": "accessory",
}


def category_for(product_type: str) -> str | None:
    """Return the app category, or None for types we don't recommend (underwear, socks, ...)."""
    return _TYPE_TO_CATEGORY.get(product_type.strip().lower())


# Free-text labels and names (ASOS, Polyvore). Checked in order, so "shirt dress" is a dress and
# "dress shoes" are shoes. Each entry: category, words that mark it.
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("shoes", ("shoes", "shoe", "boots", "boot", "sneakers", "sneaker", "trainers", "trainer", "sandals", "sandal",
               "heels", "pumps", "loafers", "loafer", "flats", "mules", "mule", "espadrilles", "ballet flats",
               "slippers", "clogs", "brogues", "slingbacks")),
    ("bag", ("bags", "bag", "handbags", "handbag", "tote", "clutch", "clutches", "backpacks", "backpack", "purse",
             "satchel", "crossbody", "cross-body", "bum bag", "wallet on chain")),
    ("dress", ("dresses", "dress", "gown", "gowns", "playsuit", "playsuits", "jumpsuit", "jumpsuits", "rompers",
               "romper")),
    ("outerwear", ("coats", "coat", "jackets", "jacket", "blazers", "blazer", "trench", "parka", "puffer", "gilet",
                   "waistcoat", "bomber", "cape", "outerwear")),
    ("bottom", ("jeans", "jean", "trousers", "pants", "skirts", "skirt", "shorts", "leggings", "joggers",
                "culottes", "cargos")),
    ("top", ("tops", "top", "t-shirt", "t-shirts", "tee", "shirts", "shirt", "blouses", "blouse", "sweaters",
             "sweater", "jumper", "jumpers", "cardigans", "cardigan", "hoodie", "hoodies", "sweatshirt", "tank",
             "cami", "camisole", "bodysuit", "polo", "knitwear", "tunic", "corset")),
    ("accessory", ("hats", "hat", "cap", "beanie", "scarves", "scarf", "belts", "belt", "sunglasses",
                   "necklaces", "necklace", "earrings", "earring", "bracelets", "bracelet", "bangles", "bangle",
                   "rings", "ring", "gloves", "hair clip", "headband")),
]
# Things the app never recommends: menswear, kidswear, underwear, swimwear, sleepwear.
_EXCLUDED = re.compile(
    r"\b(men'?s|mens|kids?|baby|toddler|maternity|lingerie|bras?|briefs|knickers|thongs?|socks|"
    r"tights|swim\w*|bikinis?|pyjamas?|pajamas?|nightwear|nightie|underwear|loungewear set|costume)\b"
)
_KEYWORD_RES = [
    (cat, re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b")) for cat, words in _KEYWORDS
]


def category_from_text(*texts: str) -> tuple[str, str] | None:
    """Classify free text: (category, the word that decided it), trying each text in turn.

    Pass the cleanest label first (e.g. a shop's category), then the product name.
    Returns None when any text marks an excluded item or nothing matches.
    """
    lowered = [t.lower() for t in texts if t]
    if any(_EXCLUDED.search(t) for t in lowered):
        return None
    for t in lowered:
        for cat, pattern in _KEYWORD_RES:
            m = pattern.search(t)
            if m:
                return cat, m.group(1)
    return None
