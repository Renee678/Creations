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
