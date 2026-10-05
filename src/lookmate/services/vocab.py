"""Shared vocabularies: style labels and body shapes, with display names."""

STYLES = {
    "old_money": "Old money",
    "quiet_luxury": "Quiet luxury",
    "minimalist": "Minimalist",
    "clean_girl": "Clean girl",
    "coquette": "Coquette",
    "ballet_core": "Balletcore",
    "streetwear": "Streetwear",
    "y2k": "Y2K",
    "preppy": "Preppy",
    "boho": "Boho",
    "office": "Office",
    "resort": "Resort",
}

# What each style means, for the stylist (services/stylist.py) and anyone reading the code.
STYLE_DEFINITIONS = {
    "old_money": "Heritage polish: navy, cream, camel, white and forest green; tailored blazers, cable knits, "
                 "pleated trousers, loafers, pearls. Classic, never trendy or flashy, no logos.",
    "quiet_luxury": "Muted and tonal: neutrals worn head to toe (cream, camel, grey, black, navy), quality-looking "
                    "fabrics (wool, silk, cashmere-look knits, satin in soft tones), clean tailoring, no logos, "
                    "no neon or bright colour blocking, at most one soft accent.",
    "minimalist": "Few pieces, solid colours, clean lines, no prints or embellishment; black, white, grey and beige.",
    "clean_girl": "Polished and effortless: fitted ribbed basics, neutral and soft tones, simple gold jewellery, "
                  "sleek accessories, nothing loud.",
    "coquette": "Romantic and feminine: soft pinks, cream and white, bows, lace, frills, ballet flats, delicate details.",
    "ballet_core": "Ballet-inspired: wrap tops, soft knits, tulle or slip skirts, ballet flats; pale pink, cream, "
                   "grey and black.",
    "streetwear": "Relaxed and urban: oversized hoodies and tees, cargos, sneakers, bold graphics allowed.",
    "y2k": "Early-2000s fun: low-rise, baby tees, mini skirts, cropped cuts, playful colour and shine allowed.",
    "preppy": "Collegiate: polo and button-down collars, pleated skirts, cable knits, loafers; navy, white, "
              "red and green accents.",
    "boho": "Free-spirited: flowing maxi and tiered pieces, crochet, florals, suede, straw; earthy warm tones.",
    "office": "Smart and practical: blazers, shirts, tailored trousers, midi skirts, low heels; neutral base.",
    "resort": "Holiday ease: linen, breezy dresses, sandals, straw bags and hats; light and sunny colours.",
}

BODY_SHAPES = {
    "hourglass": "Hourglass",
    "pear": "Pear",
    "apple": "Apple",
    "rectangle": "Rectangle",
    "inverted_triangle": "Inverted triangle",
    "unsure": "Not sure",
}


def style_name(style_id: str) -> str:
    """Label for use mid-sentence: 'old money', but acronyms like 'Y2K' keep their case."""
    label = STYLES.get(style_id, style_id)
    return label if label.isupper() else label.lower()
