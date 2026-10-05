"""Generate data/seed_products.json: a small offline catalog for tests, CI and no-network demos.

Items are combinations of hand-written garment templates and colours, so the
catalog covers every category and the common style vocabularies (old money,
minimalist, coquette, streetwear, boho, y2k, preppy) the trend module uses.
"""

import itertools
import json
from pathlib import Path

TEMPLATES = [
    # (product_type, name, description)
    ("Cardigan", "Fine-knit V-neck cardigan", "Soft fine-knit cardigan with a V-neck, buttons down the front and ribbed cuffs. Relaxed fit, cropped length. Old money, quiet luxury."),
    ("Sweater", "Cable-knit sweater", "Chunky cable-knit sweater with a round neck and dropped shoulders. Oversized fit. Preppy, cosy."),
    ("Blouse", "Satin bow blouse", "Flowing satin blouse with a tie bow at the neck and long puff sleeves. Coquette, feminine."),
    ("Shirt", "Oversized poplin shirt", "Crisp cotton poplin shirt with a collar, buttons and a boxy oversized fit. Minimalist, clean girl."),
    ("T-shirt", "Fitted rib tee", "Fitted ribbed jersey T-shirt with a round neck and short sleeves. Basic, minimalist."),
    ("Top", "Lace-trim cami top", "Cami top in satin with lace trim and thin adjustable straps. Coquette, ballet core."),
    ("Top", "Cropped baby tee", "Cropped short-sleeved T-shirt with a printed graphic. Y2K, streetwear."),
    ("Hoodie", "Oversized hoodie", "Oversized sweatshirt hoodie with a kangaroo pocket and drawstring hood. Streetwear, athleisure."),
    ("Trousers", "Wide-leg tailored trousers", "High-waisted tailored trousers with pressed pleats and wide straight legs. Old money, office."),
    ("Trousers", "Straight-leg jeans", "Five-pocket jeans in rigid denim with a high waist and straight legs. Casual, minimalist."),
    ("Trousers", "Low-rise cargo trousers", "Low-rise cargo trousers with flap pockets and a baggy fit. Y2K, streetwear."),
    ("Skirt", "Satin midi slip skirt", "Bias-cut satin midi skirt with an elasticated waist. Minimalist, quiet luxury."),
    ("Skirt", "Pleated mini skirt", "Pleated mini skirt with a high waist and concealed zip. Preppy, y2k."),
    ("Skirt", "Tiered maxi skirt", "Flowing tiered maxi skirt in cotton with an elasticated waist. Boho, beach."),
    ("Shorts", "Linen shorts", "Relaxed linen-blend shorts with a drawstring waist. Beach, resort."),
    ("Dress", "Linen maxi dress", "Sleeveless linen maxi dress with a square neckline and a flared skirt. Beach wedding, resort, boho."),
    ("Dress", "Floral tea dress", "Midi tea dress in a floral print with puff sleeves and a sweetheart neckline. Coquette, cottagecore."),
    ("Dress", "Knitted midi dress", "Fitted rib-knit midi dress with long sleeves and a round neck. Minimalist, clean girl."),
    ("Dress", "Satin slip dress", "Bias-cut satin slip dress with thin straps and a cowl neckline. Evening, quiet luxury."),
    ("Jumpsuit/Playsuit", "Wide-leg jumpsuit", "Sleeveless jumpsuit with a belted waist and wide legs. Office, minimalist."),
    ("Blazer", "Oversized blazer", "Single-breasted oversized blazer with notch lapels and padded shoulders. Old money, office."),
    ("Coat", "Wool-blend coat", "Long double-breasted coat in a wool blend with notch lapels. Old money, quiet luxury."),
    ("Jacket", "Cropped denim jacket", "Cropped jacket in washed denim with chest pockets. Casual, y2k."),
    ("Jacket", "Faux leather biker jacket", "Biker jacket in faux leather with an asymmetric zip. Streetwear, edgy."),
    ("Jacket", "Quilted puffer jacket", "Short puffer jacket with a stand-up collar. Streetwear, gorpcore."),
    ("Ballerinas", "Mary Jane ballet flats", "Ballet flats with a strap and buckle and a rounded toe. Coquette, ballet core."),
    ("Sneakers", "Leather court sneakers", "Low-profile court sneakers in faux leather. Minimalist, clean girl."),
    ("Boots", "Knee-high boots", "Knee-high boots with a block heel and pointed toe. Old money, boho."),
    ("Sandals", "Strappy flat sandals", "Flat sandals with thin straps. Beach, resort."),
    ("Pumps", "Slingback kitten heels", "Slingback pumps with a low kitten heel and pointed toe. Office, quiet luxury."),
    ("Shoulder bag", "Mini shoulder bag", "Small structured shoulder bag with a flap and a short strap. Y2K, old money."),
    ("Tote bag", "Canvas tote bag", "Large canvas tote bag with long handles. Casual, minimalist."),
    ("Cross-body bag", "Woven straw cross-body bag", "Woven straw cross-body bag with an adjustable strap. Beach, boho."),
    ("Belt", "Leather waist belt", "Narrow belt in faux leather with a gold-tone buckle. Old money."),
    ("Hat/brim", "Straw sun hat", "Wide-brimmed straw hat with a ribbon band. Beach, resort."),
    ("Hair clip", "Satin hair bow", "Satin hair bow on a clip. Coquette."),
    ("Necklace", "Pearl necklace", "Necklace with faux pearls. Old money, coquette."),
]

COLOURS = ["Black", "White", "Beige", "Light Pink", "Dark Blue", "Greige"]
COLOURS_PER_TEMPLATE = 4  # enough variety without bloating the file


def main() -> None:
    items = []
    for i, (ptype, name, desc) in enumerate(TEMPLATES):
        colours = list(itertools.islice(itertools.cycle(COLOURS), i, i + COLOURS_PER_TEMPLATE))
        for colour in colours:
            items.append({
                "id": f"seed-{len(items) + 1:04d}",
                "name": name,
                "product_type": ptype,
                "colour": colour,
                "pattern": "Solid",
                "section": "Womens",
                "description": desc,
                "image_url": "",
            })
    out = Path(__file__).resolve().parents[1] / "data" / "seed_products.json"
    out.write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(items)} items to {out}")


if __name__ == "__main__":
    main()
