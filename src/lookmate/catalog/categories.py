"""Map H&M product types onto the small set of categories the app reasons about."""

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
