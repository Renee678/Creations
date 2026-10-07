"""Make it mine: turn an inspiration look into a version for this user's colours, body and budget.

The look analysis (what each piece is) comes from the vision model, once, when the photo is uploaded.
Everything here is deterministic: each piece keeps its colour if it suits the user, or swaps to the nearest
colour of the same kind from their palette (a neutral for a neutral, an accent for an accent), with the
reason. Then the catalog is searched for that piece in the new colour within their price range, and the
outfit goes past the same stylist gate as the lookbook.
"""

import re

from ..catalog.service import Catalog
from ..llm.schemas import StylingRequest, StylistCandidate, StylistOutfit, StylistSlot
from .colours import NEUTRALS, family
from .lookbook import STYLIST_CHOICES, Palette, Stylist, _colour_family, _scored
from .price_range import PriceRange, search_in_range
from .ranking import UserContext
from .vocab import STYLE_DEFINITIONS, STYLES

CANDIDATES = 30

# Nearest families to try, in order, when a colour doesn't suit the user. Neutrals stay neutral and
# accents stay accents, so the look keeps its balance.
NEAR = {
    "beige": ["grey", "cream", "white", "navy"], "brown": ["black", "navy", "grey"],
    "cream": ["white", "grey", "beige"], "white": ["cream", "grey"], "black": ["navy", "grey", "brown"],
    "navy": ["black", "grey", "brown"], "grey": ["navy", "black", "beige"],
    "orange": ["red", "pink", "brown"], "yellow": ["cream", "white", "pink"], "green": ["blue", "navy", "purple"],
    "red": ["pink", "purple", "orange"], "pink": ["red", "purple"], "purple": ["pink", "blue", "red"],
    "blue": ["navy", "purple", "green"],
}


def adapt_colour(colour: str, palette: Palette, season: str = "") -> tuple[str, str]:
    """(colour to shop for, why). Keeps a colour that suits; otherwise the nearest one in the palette."""
    fam = family(colour)
    if not palette.personal or fam is None:
        return colour, ""
    neutral = fam in NEUTRALS
    allowed = palette.neutrals if neutral else palette.good - NEUTRALS
    if fam in allowed or (not neutral and fam in palette.good):
        return colour, f"{colour.capitalize()} already suits you"
    # The nearest family that suits, else the first palette colour of the same kind.
    near = next((n for n in NEAR.get(fam, []) if n in allowed), None)
    first = next((c for c in palette.colours if family(c) in allowed), None)
    if not (near or first):
        return colour, ""
    new = next((c for c in palette.colours if family(c) == near), near) if near else first
    if fam in palette.bad:
        why = f"{colour.capitalize()} is one of your colours to avoid; {new} keeps the look"
    else:
        why = f"{colour.capitalize()} isn't in your palette; {new} is the closest shade that is"
    return new, why + (f" and suits your {season}" if season else "")


def make_it_mine(look_result: dict, catalog: Catalog, user: UserContext, analysis: dict | None,
                 price: PriceRange | None = None, stylist: Stylist | None = None) -> dict:
    price = price or PriceRange.from_params(None, None, user.budget_per_item)
    palette = Palette.from_analysis(analysis)
    season = (analysis or {}).get("season_detail", "")
    tags = [t for t in look_result.get("style_tags", []) if t in STYLES]
    style = tags[0] if tags else "minimalist"
    used: set[str] = set()
    rows = []
    for section in look_result["sections"]:
        item = section["item"]
        colour, why = adapt_colour(item["colour"], palette, season)
        target = family(colour)
        query = _recolour(f"{item['name']}. {item['search_query']}", item["colour"], colour)
        found = (search_in_range(catalog, query, CANDIDATES, item["category"], price, exclude=used)
                 or search_in_range(catalog, query, CANDIDATES, item["category"], price))
        # The new colour is the point: keep pieces of that colour family when there are any.
        same = [c for c in found if target is None or _colour_family(c.product) == target]
        options = sorted((_scored(c, user, palette, style, item["name"], price) for c in (same or found)),
                         key=lambda p: p["score"], reverse=True)
        if not options:
            continue
        used.add(options[0]["id"])
        rows.append({"original": item, "colour": colour, "change": why, "options": options,
                     "role": "neutral" if target in NEUTRALS else "accent"})

    outfit = {"title": f"Your {STYLES[style].lower()} version", "style_id": style, "why": None,
              "reviewed": False, "approved": True}
    if stylist and rows and palette.personal:
        request = StylingRequest(setting="the user's own version of an inspiration look", palette=palette.colours,
                                 avoid=[c["name"] for c in (analysis or {}).get("avoid_colours", [])],
                                 outfits=[StylistOutfit(index=0, title=outfit["title"], style=STYLES[style],
                                                        style_definition=STYLE_DEFINITIONS[style], slots=[
            StylistSlot(slot=f"{r['colour']} {r['original']['name']}", role=r["role"], candidates=[
                StylistCandidate(id=p["id"], name=p["name"], colour=p["colour"], price=p["price"])
                for p in r["options"][:STYLIST_CHOICES]]) for r in rows])])
        verdict = stylist(request).get(0)
        if verdict:
            picks, line, approved = verdict
            for r, pick in zip(rows, picks):
                chosen = next((p for p in r["options"] if p["id"] == pick), None)
                if chosen:
                    r["options"].remove(chosen)
                    r["options"].insert(0, chosen)
            outfit.update(reviewed=True, approved=approved, why=line or None)
    pieces = [{"original": r["original"], "colour": r["colour"], "change": r["change"], "pick": r["options"][0]}
              for r in rows]
    return {**outfit, "personal": palette.personal, "vibe": look_result.get("vibe", ""), "pieces": pieces,
            "total_price": round(sum(p["pick"]["price"] for p in pieces), 2), "price_range": price.to_dict()}


def _recolour(text: str, old: str, new: str) -> str:
    if old.lower() == new.lower():
        return text
    swapped = re.sub(re.escape(old), new, text, flags=re.I)
    return swapped if swapped != text else f"{new} {text}"
