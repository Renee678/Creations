"""A soft price range: the user's range first, widened step by step only when it comes up empty.

A hard filter often returns nothing (few $10 coats exist), and an unfiltered search lets a $260
designer pump win a slot. So searches try the range as given, then roughly half to 1.5x, then a
quarter to 3x, then any price. Picks from a widened step are labelled, so the user sees why.
"""

from dataclasses import dataclass

from ..catalog.service import Catalog, SearchResult

DEFAULT_STRETCH = 1.5  # with no range chosen: anything up to 1.5x the per-item budget


@dataclass(frozen=True)
class PriceRange:
    low: float
    high: float

    @classmethod
    def from_params(cls, low: float | None, high: float | None, budget: float) -> "PriceRange":
        high = high if high is not None else round(budget * DEFAULT_STRETCH)
        low = low if low is not None else 0.0
        return cls(min(low, high), max(low, high))

    def steps(self) -> list[tuple[float | None, float | None]]:
        return [(self.low, self.high), (self.low * 0.5, self.high * 1.5), (self.low * 0.25, self.high * 3), (None, None)]

    def note(self, price: float) -> str | None:
        """Why a pick sits outside the range, or None if it's inside."""
        if price > self.high:
            return "A bit above your price range" if price <= self.high * 1.5 else "Above your price range: closest match"
        if price < self.low:
            return "Below your price range"
        return None

    def to_dict(self) -> dict:
        return {"low": self.low, "high": self.high}


def search_in_range(catalog: Catalog, query: str, k: int, category: str | None, price: PriceRange,
                    want: int = 1, exclude: set[str] | None = None) -> list[SearchResult]:
    """Candidates from the tightest step that yields at least `want` of them."""
    found: dict[str, SearchResult] = {}
    for low, high in price.steps():
        for r in catalog.search(query, k=k, category=category, min_price=low, max_price=high, exclude=exclude):
            found.setdefault(r.product.id, r)
        if len(found) >= want:
            break
    return list(found.values())
