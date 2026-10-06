"""The price range shared by Find dupes and the Lookbook: a hard filter."""

import numpy as np

from lookmate.catalog.index import VectorIndex
from lookmate.services.price_range import PriceRange, search_in_range


def test_range_defaults_to_the_budget_and_orders_its_ends():
    assert PriceRange.from_params(None, None, 30) == PriceRange(0, 30), "Renee: the range equals the budget"
    assert PriceRange.from_params(None, None, 50) == PriceRange(0, 50)
    assert PriceRange.from_params(80, 20, 30) == PriceRange(20, 80)


def test_the_empty_range_note_names_the_range():
    r = PriceRange(0, 85)
    assert r.label() == "$0–$85"
    assert r.empty_note() == "Nothing in $0–$85 matches this piece. Widen the price range to see more."


def test_index_filters_by_minimum_price():
    idx = VectorIndex(["a", "b", "c"], np.eye(3, dtype=np.float32), ["top"] * 3, [5, 25, 90])
    q = np.ones(3, dtype=np.float32)
    assert {h.product_id for h in idx.search(q, k=5, min_price=10, max_price=50)} == {"b"}


def test_search_never_leaves_the_range(runtime):
    """Renee (2026-10-06): nothing outside the slider's range is ever shown, not even as a closest match."""
    coats = sorted(p.price for p in runtime.catalog.products.values() if p.category == "outerwear")
    assert search_in_range(runtime.catalog, "coat", 10, "outerwear", PriceRange(0, coats[0] / 10)) == []
    res = search_in_range(runtime.catalog, "coat", 10, "outerwear", PriceRange(coats[0], coats[len(coats) // 2]))
    assert res and all(coats[0] <= r.product.price <= coats[len(coats) // 2] for r in res)
