"""Personal lookbooks: complete outfits per season or per occasion, built from the catalog.

The model only perceives (the PersonAnalysis: colour season, palette, face shape).
Everything here is deterministic: outfit formulas are fixed templates, each one is
flavoured with one of the user's styles or a current trend, coloured from the user's
palette, and every slot is filled by vector search plus a transparent score.

score = 0.55 * similarity to the slot description
      + 0.15 * palette    (+1 in the user's colours, -1 in the colours to avoid)
      + 0.10 * style match
      + 0.10 * body-shape fit
      + 0.10 * price score
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog.service import Catalog
from ..models import PersonalAnalysis
from .body import fit_adjustment
from .colours import NEUTRALS, families, family, palette_score
from .ranking import STYLE_KEYWORDS, UserContext, price_score, style_score
from .vocab import BODY_SHAPES, STYLES, style_name

W_SIM, W_PALETTE, W_STYLE, W_FIT, W_PRICE = 0.55, 0.15, 0.10, 0.10, 0.10
CANDIDATES_PER_SLOT = 30

Slot = tuple[str, str]  # (category, garment description)

SEASONS: dict[str, tuple[str, list[list[Slot]]]] = {
    "spring": ("Spring", [
        [("outerwear", "light trench jacket"), ("top", "fine knit top"), ("bottom", "straight-leg trousers"),
         ("shoes", "ballet flats")],
        [("dress", "floral midi dress"), ("outerwear", "cropped denim jacket"), ("shoes", "white sneakers")],
        [("top", "cotton blouse"), ("bottom", "pleated midi skirt"), ("bag", "shoulder bag"), ("shoes", "pumps")],
    ]),
    "summer": ("Summer", [
        [("dress", "linen sundress"), ("bag", "straw tote bag"), ("shoes", "flat sandals")],
        [("top", "fitted rib tank top"), ("bottom", "linen shorts"), ("shoes", "sandals"), ("accessory", "sun hat")],
        [("top", "short-sleeve shirt"), ("bottom", "wide-leg linen trousers"), ("bag", "cross-body bag")],
    ]),
    "autumn": ("Autumn", [
        [("outerwear", "wool blazer"), ("top", "knit sweater"), ("bottom", "wide-leg trousers"),
         ("shoes", "ankle boots")],
        [("dress", "long-sleeve midi dress"), ("outerwear", "suede jacket"), ("shoes", "knee-high boots")],
        [("top", "cardigan"), ("bottom", "jeans"), ("bag", "shoulder bag"), ("shoes", "loafers")],
    ]),
    "winter": ("Winter", [
        [("outerwear", "long wool coat"), ("top", "turtleneck sweater"), ("bottom", "tailored trousers"),
         ("shoes", "leather boots")],
        [("outerwear", "padded jacket"), ("top", "hoodie"), ("bottom", "straight jeans"), ("shoes", "chunky sneakers")],
        [("dress", "knit dress"), ("outerwear", "wool coat"), ("accessory", "scarf"), ("shoes", "boots")],
    ]),
}

OCCASIONS: dict[str, tuple[str, list[list[Slot]]]] = {
    "work": ("Work", [
        [("outerwear", "tailored blazer"), ("top", "silk blouse"), ("bottom", "tailored trousers"),
         ("shoes", "pumps"), ("bag", "tote bag")],
        [("dress", "sheath midi dress"), ("outerwear", "cardigan"), ("shoes", "loafers")],
    ]),
    "weekend": ("Weekend", [
        [("top", "relaxed t-shirt"), ("bottom", "straight-leg jeans"), ("shoes", "sneakers"),
         ("bag", "cross-body bag")],
        [("top", "hoodie"), ("bottom", "wide-leg trousers"), ("shoes", "sneakers")],
    ]),
    "date": ("Date night", [
        [("dress", "satin slip dress"), ("shoes", "heeled sandals"), ("bag", "small shoulder bag")],
        [("top", "lace top"), ("bottom", "midi skirt"), ("shoes", "pumps"), ("accessory", "necklace")],
    ]),
    "party": ("Party", [
        [("dress", "sequin mini dress"), ("shoes", "heels"), ("bag", "clutch bag")],
        [("top", "corset top"), ("bottom", "leather trousers"), ("shoes", "heeled boots")],
    ]),
    "travel": ("Vacation", [
        [("dress", "linen maxi dress"), ("shoes", "sandals"), ("accessory", "straw hat")],
        [("top", "linen shirt"), ("bottom", "shorts"), ("shoes", "sneakers"), ("bag", "tote bag")],
    ]),
}

DEFAULT_PALETTE = ["black", "white", "beige", "navy", "grey"]
DEFAULT_STYLES = ["minimalist", "clean_girl", "old_money"]


@dataclass(frozen=True)
class Palette:
    colours: list[str]   # names, used in search queries
    good: set[str]       # colour families to favour
    bad: set[str]        # colour families to avoid
    personal: bool      # False when there is no analysis yet

    @classmethod
    def from_analysis(cls, analysis: dict | None) -> "Palette":
        if not analysis:
            return cls(DEFAULT_PALETTE, set(), set(), personal=False)
        best = [s["name"] for s in analysis.get("best_colours", [])] or DEFAULT_PALETTE
        good = families(best)
        bad = families([s["name"] for s in analysis.get("avoid_colours", [])]) - good
        return cls(best, good, bad, personal=True)

    def colours_for(self, outfit_no: int, n_slots: int) -> list[str]:
        """The first slot gets an accent colour; the rest are neutrals, so outfits stay wearable."""
        accents = [c for c in self.colours if family(c) not in NEUTRALS] or self.colours
        neutrals = [c for c in self.colours if family(c) in NEUTRALS] or self.colours
        return [accents[outfit_no % len(accents)]] + [neutrals[(outfit_no + i) % len(neutrals)] for i in range(n_slots - 1)]


def latest_analysis(session: Session, user_id: int) -> PersonalAnalysis | None:
    return session.scalar(
        select(PersonalAnalysis)
        .where(PersonalAnalysis.user_id == user_id, PersonalAnalysis.status == "done")
        .order_by(PersonalAnalysis.finished_at.desc(), PersonalAnalysis.id.desc())
        .limit(1)
    )


def outfit_styles(user: UserContext, analysis: dict | None, trends: list[tuple[str, str]]) -> list[str]:
    """Styles to rotate through: the user's strongest, then what suits them, then what's trending."""
    ranked = [s for s, w in sorted(user.style_weights.items(), key=lambda kv: kv[1], reverse=True) if w > 0][:2]
    ranked += [s for s in (analysis or {}).get("style_tags", []) if s in STYLES][:1]
    ranked += [s for s, _ in trends]
    ranked += DEFAULT_STYLES
    return list(dict.fromkeys(ranked))[:3]


def build_lookbook(
    catalog: Catalog, user: UserContext, analysis_rec: PersonalAnalysis | None,
    trends: list[tuple[str, str]], mode: str = "seasons",
) -> dict:
    analysis = analysis_rec.result if analysis_rec else None
    palette = Palette.from_analysis(analysis)
    styles = outfit_styles(user, analysis, trends)
    trend_labels = dict(trends)
    templates = SEASONS if mode == "seasons" else OCCASIONS
    used: set[str] = set()

    sections = []
    for section_id, (title, formulas) in templates.items():
        outfits = []
        for n, slots in enumerate(formulas):
            style = styles[n % len(styles)]
            colours = palette.colours_for(n + len(sections), len(slots))
            pieces = [p for p in (
                _fill_slot(catalog, user, palette, style, cat, desc, colour, used)
                for (cat, desc), colour in zip(slots, colours)
            ) if p]
            if not pieces:
                continue
            outfits.append({
                "title": f"{STYLES[style]} {title.lower()}" if mode == "seasons" else f"{STYLES[style]} · {title}",
                "style_id": style,
                "trend": trend_labels.get(style),
                "pieces": pieces,
                "total_price": round(sum(p["price"] for p in pieces), 2),
            })
        sections.append({"id": section_id, "title": title, "outfits": outfits})

    return {
        "mode": mode,
        "personal": palette.personal,
        "analysis": analysis,
        "styles": [{"id": s, "label": STYLES[s]} for s in styles],
        "sections": sections,
    }


def _fill_slot(catalog: Catalog, user: UserContext, palette: Palette, style: str,
               category: str, desc: str, colour: str, used: set[str]) -> dict | None:
    flavour = " ".join(STYLE_KEYWORDS.get(style, [])[:2])
    query = f"{colour} {desc} {flavour}"
    candidates = catalog.search(query, k=CANDIDATES_PER_SLOT, category=category, exclude=used)
    if not candidates:  # small catalogs run out: reuse a piece rather than leave a gap
        candidates = catalog.search(query, k=CANDIDATES_PER_SLOT, category=category)
    best = None
    for c in candidates:
        p = c.product
        text = f"{p.name} {p.product_type} {p.description}"
        s_pal = palette_score(p.colour, palette.good, palette.bad)
        s_style, _ = style_score([style], user, text)
        s_fit = fit_adjustment(user.body_shape, p.category, text)
        score = (W_SIM * c.score + W_PALETTE * s_pal + W_STYLE * s_style + W_FIT * s_fit
                 + W_PRICE * price_score(p.price, user.budget_per_item))
        if best is None or score > best[0]:
            best = (score, c, s_pal, s_style, s_fit)
    if best is None:
        return None
    score, c, s_pal, s_style, s_fit = best
    p = c.product
    used.add(p.id)

    reasons = []
    if s_pal > 0 and palette.personal:
        reasons.append("In your colour palette")
    if s_style > 0:
        mine = user.style_weights.get(style, 0) > 0
        reasons.append(f"Matches your {style_name(style)} style" if mine else f"Gives a {style_name(style)} feel")
    if s_fit > 0:
        reasons.append(f"Cut suits your {BODY_SHAPES.get(user.body_shape, '').lower()} shape")
    if p.price <= user.budget_per_item:
        reasons.append("Within your budget")
    return {**p.to_dict(), "slot": desc, "score": round(score, 4), "reasons": reasons}
