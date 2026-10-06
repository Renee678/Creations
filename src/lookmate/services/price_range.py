"""The price range is a hard filter: nothing outside the slider's range is ever shown (Renee, 2026-10-06).

It used to widen step by step when the range came up empty, labelling the pricier picks; a $190 pair of
jeans then answered a $0-$85 search. Now an empty range says so and asks the user to widen it.
"""

from collections.abc import Callable
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

    def label(self) -> str:
        """'$0–$85', as the slider shows it."""
        return f"${self.low:,.0f}–${self.high:,.0f}"

    def empty_note(self, what: str = "this piece") -> str:
        return f"Nothing in {self.label()} matches {what}. Widen the price range to see more."

    def to_dict(self) -> dict:
        return {"low": self.low, "high": self.high}


def search_in_range(catalog: Catalog, query: str, k: int, category: str | None, price: PriceRange | None,
                    exclude: set[str] | None = None,
                    accept: Callable[[SearchResult], bool] | None = None) -> list[SearchResult]:
    """Candidates priced inside the range (any price with `price=None`); with `accept`, only those it passes."""
    low, high = (price.low, price.high) if price else (None, None)
    return [r for r in catalog.search(query, k=k, category=category, min_price=low, max_price=high, exclude=exclude)
            if accept is None or accept(r)]
