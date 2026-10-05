"""From a look analysis to per-item affordable alternatives."""

from ..catalog.service import Catalog
from ..llm.schemas import LookAnalysis
from dataclasses import replace

from .match import Target, length
from .price_range import PriceRange, search_in_range
from .ranking import RankedPick, UserContext, rank

CANDIDATES_PER_ITEM = 150  # a wide pool, because the type, colour and length rules then remove most of it
PICKS_PER_ITEM = 4


def find_dupes(analysis: LookAnalysis, catalog: Catalog, user: UserContext, price: PriceRange | None = None) -> dict:
    """Return a JSON-serialisable result: one section per detected item."""
    price = price or PriceRange.from_params(None, None, user.budget_per_item)
    user = replace(user, budget_per_item=price.high)  # the price score aims at the top of the range
    sections = []
    used: set[str] = set()  # never show the same product for two items
    # Main pieces first, so a sliver at the photo's edge never takes a product from them. Partial pieces
    # still get a section, but it is hidden until the user asks for it.
    for item in sorted(analysis.items, key=lambda i: i.partial):
        query = f"{item.colour} {item.name}. {item.search_query}. {' '.join(item.details)}"
        target = Target(item.category, item.name, item.colour, item.details, item.fit)
        # Accuracy over a full page (Renee): same garment type, colour and length, always. Only the price
        # range widens; if nothing in the catalog matches, the section says so instead of showing near-misses.
        candidates = search_in_range(catalog, query, CANDIDATES_PER_ITEM, item.category, price, want=PICKS_PER_ITEM,
                                     exclude=used, accept=lambda r: target.check(r.product))
        picks: list[RankedPick] = rank(item, candidates, user, k=PICKS_PER_ITEM)
        used.update(p.product_id for p in picks)
        note = None
        if len(picks) < PICKS_PER_ITEM:
            note = (f"Only {len(picks)} close {'match' if len(picks) == 1 else 'matches'} for this piece. "
                    "Widen the price range to see more." if picks
                    else f"No {_describe(target, item)} in our catalog yet, so nothing is shown rather than a wrong match.")
        sections.append({
            "item": item.model_dump(),
            "note": note,
            "hidden": item.partial,
            "picks": [
                {
                    **catalog.products[p.product_id].to_dict(),
                    "score": p.score,
                    # The price label first: a card shows the first reasons, and this one must not be cut off.
                    "reasons": [n for n in [price.note(catalog.products[p.product_id].price)] if n]
                    + [r for r in p.reasons if not (r == "Within your budget" and price.note(catalog.products[p.product_id].price))]
                    + _length_reason(target, catalog.products[p.product_id]),
                    "saving_usd": (
                        round(item.estimated_original_price_usd - catalog.products[p.product_id].price, 2)
                        if item.estimated_original_price_usd else None
                    ),
                }
                for p in picks
            ],
        })
    return {"vibe": analysis.vibe, "style_tags": analysis.style_tags, "sections": sections,
            "price_range": price.to_dict()}


def _describe(target: Target, item) -> str:
    """'white maxi skirt': the rules the catalog couldn't meet, in the shopper's words."""
    words = [item.colour.lower() if target.colour else "", target.length, target.subtype]
    return " ".join(w for w in words if w) if target.subtype else item.name


def _length_reason(target: Target, product) -> list[str]:
    theirs = length(f"{product.name} {product.product_type}")
    if not target.length or not theirs:
        return []
    if theirs == target.length:
        return [f"Same {target.length} length"]
    return [f"{theirs.capitalize()} rather than {target.length}"]
