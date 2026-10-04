"""From a look analysis to per-item affordable alternatives."""

from ..catalog.service import Catalog
from ..llm.schemas import LookAnalysis
from .ranking import RankedPick, UserContext, rank

CANDIDATES_PER_ITEM = 25
PICKS_PER_ITEM = 4


def find_dupes(analysis: LookAnalysis, catalog: Catalog, user: UserContext) -> dict:
    """Return a JSON-serialisable result: one section per detected item."""
    sections = []
    used: set[str] = set()  # never show the same product for two items
    for item in analysis.items:
        query = f"{item.colour} {item.name}. {item.search_query}. {' '.join(item.details)}"
        candidates = catalog.search(query, k=CANDIDATES_PER_ITEM, category=item.category, exclude=used)
        picks: list[RankedPick] = rank(item, candidates, user, k=PICKS_PER_ITEM)
        used.update(p.product_id for p in picks)
        sections.append({
            "item": item.model_dump(),
            "picks": [
                {
                    **catalog.products[p.product_id].__dict__,
                    "score": p.score,
                    "reasons": p.reasons,
                    "saving_usd": (
                        round(item.estimated_original_price_usd - catalog.products[p.product_id].price, 2)
                        if item.estimated_original_price_usd else None
                    ),
                }
                for p in picks
            ],
        })
    return {"vibe_zh": analysis.vibe_zh, "style_tags": analysis.style_tags, "sections": sections}
