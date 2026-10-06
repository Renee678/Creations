"""Personal lookbooks: complete outfits per season or per occasion, built from the catalog.

Retrieval and scoring are deterministic: outfit formulas are fixed templates, each one is
flavoured with one of the user's styles or a current trend, coloured from the user's
palette, and every slot is filled by vector search plus a transparent score. One slot per
outfit carries the accent colour; the others only accept true neutrals, so two loud pieces
never meet. Optionally a stylist (stylist.py) then picks, per slot, among the top few
candidates so the outfit works as a whole; without it the top-scored piece wins.

score = 0.55 * similarity to the slot description
      + 0.15 * palette    (+1 in the user's colours, -1 in the colours to avoid)
      + 0.10 * style match
      + 0.10 * body-shape fit
      + 0.10 * price score
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

import logging
import re
from collections.abc import Callable

from ..catalog.service import Catalog
from ..llm.schemas import StylingRequest, StylistCandidate, StylistOutfit, StylistSlot
from ..models import PersonalAnalysis
from .body import fit_adjustment
from .colours import NEUTRALS, colour_word, families, family, palette_score
from .price_range import PriceRange, search_in_range
from .ranking import STYLE_KEYWORDS, UserContext, price_score, style_score
from .vocab import BODY_SHAPES, STYLE_DEFINITIONS, STYLES, style_name

log = logging.getLogger(__name__)

W_SIM, W_PALETTE, W_STYLE, W_FIT, W_PRICE = 0.55, 0.15, 0.10, 0.10, 0.10
W_CLEAN_PHOTO = 0.02  # tie-breaker: a white-background product photo wins between near-equal candidates
CANDIDATES_PER_SLOT = 30
STYLIST_CHOICES = 5  # top candidates per slot the stylist may choose from

# Neutral slots take only these. Camel and brown join when the palette holds them (warm palettes).
CORE_NEUTRALS = {"black", "white", "grey", "cream", "navy"}
WARM_NEUTRALS = {"beige", "brown"}
# Never in a neutral slot, nor anywhere in a muted style, whatever the colour family says.
LOUD = re.compile(r"\b(neon|fluro|fluoro|fluorescent|bright|metallic|sequin\w*|glitter\w*|holographic|vinyl|"
                  r"high[- ]shine|tie[- ]dye|electric|hot pink|lime)\b", re.I)
MUTED_STYLES = {"quiet_luxury", "old_money", "minimalist", "clean_girl"}

Stylist = Callable[[StylingRequest], dict[int, tuple[list[str], str, bool]]]

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

    @property
    def neutrals(self) -> set[str]:
        """Colour families a neutral slot accepts."""
        warm = WARM_NEUTRALS if (not self.personal or self.good & WARM_NEUTRALS) else set()
        return (CORE_NEUTRALS | warm) - self.bad

    def colours_for(self, outfit_no: int, n_slots: int) -> list[str]:
        """The first slot gets an accent colour; the rest are neutrals, so outfits stay wearable."""
        accents = [c for c in self.colours if family(c) not in NEUTRALS] or self.colours
        neutrals = [c for c in self.colours if family(c) in NEUTRALS] or self.colours
        return [accents[outfit_no % len(accents)]] + [neutrals[(outfit_no + i) % len(neutrals)] for i in range(n_slots - 1)]


VIBE_WORDS = {
    "seasons": {"spring": "spring", "summer": "summer", "autumn": "autumn", "fall": "autumn", "winter": "winter"},
    "occasions": {"work": "work", "office": "work", "weekend": "weekend", "casual": "weekend", "date": "date",
                  "party": "party", "night out": "party", "vacation": "travel", "holiday": "travel",
                  "travel": "travel", "beach": "travel"},
}


def parse_vibe(text: str) -> tuple[str | None, str | None, str | None]:
    """(style, season, occasion) named in a free-text vibe; each None when not named. Deterministic."""
    t = f" {text.lower().replace('-', ' ')} "
    style = next((s for s, label in STYLES.items() if f" {label.lower()} " in t or f" {s.replace('_', ' ')} " in t),
                 None)
    if style is None:  # a keyword, e.g. "cashmere" or "ballet flats"
        style = next((s for s, words in STYLE_KEYWORDS.items() if any(f" {w} " in t for w in words)), None)
    season = next((v for k, v in VIBE_WORDS["seasons"].items() if f" {k} " in t), None)
    occasion = next((v for k, v in VIBE_WORDS["occasions"].items() if f" {k} " in t), None)
    return style, season, occasion


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
    trends: list[tuple[str, str]], mode: str = "seasons", price: PriceRange | None = None,
    season: str | None = None, stylist: Stylist | None = None, vibe: str | None = None,
    occasion: str | None = None,
) -> dict:
    # A soft price range (see price_range.py): without it a $260 designer pump can win a slot,
    # since price is only 10% of the score.
    price = price or PriceRange.from_params(None, None, user.budget_per_item)
    analysis = analysis_rec.result if analysis_rec else None
    palette = Palette.from_analysis(analysis)
    styles = outfit_styles(user, analysis, trends)
    trend_labels = dict(trends)
    templates = SEASONS if mode == "seasons" else OCCASIONS
    if vibe:
        # "quiet luxury autumn", "date night coquette": that style, for that season or occasion.
        v_style, v_season, v_occasion = parse_vibe(vibe)
        styles = [v_style] if v_style else styles
        if v_occasion:
            mode, templates = "occasions", {v_occasion: OCCASIONS[v_occasion]}
        else:
            mode, templates, season = "seasons", SEASONS, v_season or season
    if mode == "seasons" and season in SEASONS:
        templates = {season: SEASONS[season]}  # one season at a time: the current one unless asked
    if mode == "occasions" and occasion in OCCASIONS and templates is OCCASIONS:
        templates = {occasion: OCCASIONS[occasion]}  # the occasion picked before "Create my looks"
    used: set[str] = set()

    sections = []
    for section_id, (title, formulas) in templates.items():
        outfits = []
        for n, slots in enumerate(formulas):
            style = styles[n % len(styles)]
            colours = palette.colours_for(n + len(sections), len(slots))
            filled = []
            for i, ((cat, desc), colour) in enumerate(zip(slots, colours)):
                options = _slot_options(catalog, user, palette, style, cat, desc, colour, used, price, neutral=i > 0)
                if options:
                    used.add(options[0]["id"])
                    filled.append((desc, "neutral" if i > 0 else "accent", options))
            if not filled:
                continue
            outfits.append({
                "title": f"{STYLES[style]} {title.lower()}" if mode == "seasons" else f"{STYLES[style]} · {title}",
                "style_id": style,
                "trend": trend_labels.get(style),
                "why": None,
                "reviewed": False,  # True once the stylist has approved it
                "slots": filled,
            })
        sections.append({"id": section_id, "title": title, "outfits": outfits})

    if stylist:
        _apply_stylist(stylist, sections, palette, analysis)
    for section in sections:
        for outfit in section["outfits"]:
            pieces = [opts[0] for _, _, opts in outfit.pop("slots")]
            outfit["pieces"] = pieces
            outfit["total_price"] = round(sum(p["price"] for p in pieces), 2)

    return {
        "mode": mode,
        "vibe": vibe or None,
        "season": next(iter(templates)) if mode == "seasons" else None,
        "occasion": next(iter(templates)) if mode == "occasions" and len(templates) == 1 else None,
        "personal": palette.personal,
        "analysis": analysis,
        "styles": [{"id": s, "label": STYLES[s]} for s in styles],
        "sections": sections,
        "price_range": price.to_dict(),
        "styled": any(o["reviewed"] for s in sections for o in s["outfits"]),
    }


def _apply_stylist(stylist: Stylist, sections: list[dict], palette: Palette, analysis: dict | None) -> None:
    """Ask the stylist once for the whole page; move each chosen piece to the front of its slot's options."""
    outfits = [o for s in sections for o in s["outfits"]]
    if not outfits:
        return
    request = StylingRequest(
        setting=", ".join(s["title"].lower() for s in sections),
        palette=palette.colours,
        avoid=[c["name"] for c in (analysis or {}).get("avoid_colours", [])],
        outfits=[StylistOutfit(
            index=i, title=o["title"], style=STYLES[o["style_id"]], style_definition=STYLE_DEFINITIONS[o["style_id"]],
            slots=[StylistSlot(slot=desc, role=role, candidates=[
                StylistCandidate(id=p["id"], name=p["name"], colour=p["colour"], price=p["price"])
                for p in opts[:STYLIST_CHOICES]]) for desc, role, opts in o["slots"]],
        ) for i, o in enumerate(outfits)],
    )
    verdicts = stylist(request)
    if not verdicts:
        return  # the stylist couldn't run: the scorer's picks stand, marked unreviewed
    for i, outfit in enumerate(outfits):
        if i not in verdicts:
            outfit["rejected"] = True  # the stylist reviewed the page but didn't pass this one
            continue
        picks, why, approved = verdicts[i]
        for (_, _, opts), pick in zip(outfit["slots"], picks):
            chosen = next((p for p in opts if p["id"] == pick), None)
            if chosen:
                opts.remove(chosen)
                opts.insert(0, chosen)
        outfit["reviewed"] = True
        outfit["why"] = (why or None) if approved else None
        outfit["rejected"] = not approved
        if not approved:
            log.info("stylist rejected %r: %s", outfit["title"], why)
    for section in sections:
        section["outfits"] = [o for o in section["outfits"] if not o.pop("rejected", False)]


def _colour_family(p) -> str | None:
    return family(p.colour) if p.colour else family(colour_word(p.name))


def _fill_slot(catalog: Catalog, user: UserContext, palette: Palette, style: str,
               category: str, desc: str, colour: str, used: set[str], price: PriceRange | None = None,
               neutral: bool | None = None) -> dict | None:
    """The top-scored piece for a slot, or None."""
    if neutral is None:
        neutral = family(colour) in NEUTRALS
    options = _slot_options(catalog, user, palette, style, category, desc, colour, used, price, neutral)
    if not options:
        return None
    used.add(options[0]["id"])
    return options[0]


def _slot_options(catalog: Catalog, user: UserContext, palette: Palette, style: str,
                  category: str, desc: str, colour: str, used: set[str], price: PriceRange | None = None,
                  neutral: bool = False) -> list[dict]:
    """Candidates for a slot that pass the colour rules, best score first."""
    price = price or PriceRange.from_params(None, None, user.budget_per_item)
    flavour = " ".join(STYLE_KEYWORDS.get(style, [])[:2])
    queries = [f"{colour} {desc} {flavour}"]
    if neutral:
        # Vector search reads "navy trousers" loosely and can return bright blue ones; a second,
        # plainer query keeps a neutral slot from coming back empty after the colour filter.
        queries.append(f"black {desc}")
    for query in queries:
        candidates = (
            search_in_range(catalog, query, CANDIDATES_PER_SLOT, category, price, exclude=used, any_price=False)
            # small catalogs run out: reuse a piece rather than leave a gap
            or search_in_range(catalog, query, CANDIDATES_PER_SLOT, category, price, any_price=False)
        )
        scored = [_scored(c, user, palette, style, desc, price) for c in candidates
                  if _colour_ok(c.product, palette, style, neutral)]
        if scored:
            # A packshot on white makes a cleaner flat lay than a model shot: a nudge between close
            # candidates, never a filter (Renee, feedback #34).
            return sorted(scored, key=lambda p: p["score"] + (W_CLEAN_PHOTO if p["white_background"] else 0),
                          reverse=True)
    return []


def _colour_ok(p, palette: Palette, style: str, neutral: bool) -> bool:
    text = f"{p.name} {p.colour}"
    fam = _colour_family(p)
    if neutral:
        return fam in palette.neutrals and not LOUD.search(text)
    if fam in palette.bad:
        return False
    return not (style in MUTED_STYLES and LOUD.search(text))


def _scored(c, user: UserContext, palette: Palette, style: str, desc: str, price: PriceRange) -> dict:
    p = c.product
    text = f"{p.name} {p.product_type} {p.description}"
    s_pal = palette_score(p.colour, palette.good, palette.bad)
    s_style, _ = style_score([style], user, text)
    s_fit = fit_adjustment(user.body_shape, p.category, text)
    score = (W_SIM * c.score + W_PALETTE * s_pal + W_STYLE * s_style + W_FIT * s_fit
             + W_PRICE * price_score(p.price, price.high))

    reasons = []
    if s_pal > 0 and palette.personal:
        reasons.append("In your colour palette")
    if s_style > 0:
        mine = user.style_weights.get(style, 0) > 0
        reasons.append(f"Matches your {style_name(style)} style" if mine else f"Gives a {style_name(style)} feel")
    if s_fit > 0:
        reasons.append(f"Cut suits your {BODY_SHAPES.get(user.body_shape, '').lower()} shape")
    note = price.note(p.price)
    reasons.append(note or "In your price range")
    return {**p.to_dict(), "slot": desc, "score": round(score, 4), "reasons": reasons}
