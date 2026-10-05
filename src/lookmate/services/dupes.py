"""From a look analysis to per-item affordable alternatives."""

from ..catalog.service import Catalog
from ..llm.schemas import LookAnalysis
from dataclasses import replace

from .match import Target, length
from .price_range import PriceRange, search_in_range
from .ranking import RankedPick, UserContext, rank

CANDIDATES_PER_ITEM = 150  # a wide pool, because the type, colour and length rules then remove most of it
PICKS_PER_ITEM = 4
MIN_CLOSE = 2  # fewer exact matches than this: relax length a step, then colour, and say so


def find_dupes(analysis: LookAnalysis, catalog: Catalog, user: UserContext, price: PriceRange | None = None) -> dict:
    """Return a JSON-serialisable result: one section per detected item."""
    price = price or PriceRange.from_params(None, None, user.budget_per_item)
    user = replace(user, budget_per_item=price.high)  # the price score aims at the top of the range
    sections = []
    used: set[str] = set()  # never show the same product for two items
    for item in analysis.items:
        query = f"{item.colour} {item.name}. {item.search_query}. {' '.join(item.details)}"
        target = Target(item.category, item.name, item.colour, item.details, item.fit)
        # Same garment type always; same colour and length first. Relax length by one step, then colour,
        # only when too few exact matches exist, and never the garment type: a skirt is never trousers.
        candidates, relaxed = [], None
        for strict_colour, gap, label in ((True, 0, None), (True, 1, "length"), (False, 1, "colour")):
            found = search_in_range(catalog, query, CANDIDATES_PER_ITEM, item.category, price, want=PICKS_PER_ITEM,
                                    exclude=used, accept=lambda r: target.check(r.product, strict_colour, gap))
            candidates, relaxed = found, label
            if len(found) >= MIN_CLOSE:
                break
        picks: list[RankedPick] = rank(item, candidates, user, k=PICKS_PER_ITEM)
        used.update(p.product_id for p in picks)
        note = None
        if len(picks) < PICKS_PER_ITEM:
            note = (f"Only {len(picks)} close {'match' if len(picks) == 1 else 'matches'} for this piece. "
                    "Widen the price range to see more." if picks
                    else "No close match for this piece in the catalog yet.")
        sections.append({
            "item": item.model_dump(),
            "note": note,
            "relaxed": relaxed,
            "picks": [
                {
                    **catalog.products[p.product_id].to_dict(),
                    "score": p.score,
                    "reasons": p.reasons + _length_reason(target, catalog.products[p.product_id])
                    + [n for n in [price.note(catalog.products[p.product_id].price)] if n],
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


def _length_reason(target: Target, product) -> list[str]:
    theirs = length(f"{product.name} {product.product_type}")
    if not target.length or not theirs:
        return []
    if theirs == target.length:
        return [f"Same {target.length} length"]
    return [f"{theirs.capitalize()} rather than {target.length}"]
