"""From a look analysis to per-item affordable alternatives."""

from ..catalog.service import Catalog
from ..llm.schemas import LookAnalysis
from dataclasses import replace

from .price_range import PriceRange, search_in_range
from .ranking import RankedPick, UserContext, rank

CANDIDATES_PER_ITEM = 25
PICKS_PER_ITEM = 4


def find_dupes(analysis: LookAnalysis, catalog: Catalog, user: UserContext, price: PriceRange | None = None) -> dict:
    """Return a JSON-serialisable result: one section per detected item."""
    price = price or PriceRange.from_params(None, None, user.budget_per_item)
    user = replace(user, budget_per_item=price.high)  # the price score aims at the top of the range
    sections = []
    used: set[str] = set()  # never show the same product for two items
    for item in analysis.items:
        query = f"{item.colour} {item.name}. {item.search_query}. {' '.join(item.details)}"
        candidates = search_in_range(catalog, query, CANDIDATES_PER_ITEM, item.category, price,
                                     want=PICKS_PER_ITEM, exclude=used)
        picks: list[RankedPick] = rank(item, candidates, user, k=PICKS_PER_ITEM)
        used.update(p.product_id for p in picks)
        sections.append({
            "item": item.model_dump(),
            "picks": [
                {
                    **catalog.products[p.product_id].to_dict(),
                    "score": p.score,
                    "reasons": p.reasons + [n for n in [price.note(catalog.products[p.product_id].price)] if n],
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
