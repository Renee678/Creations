"""The soft price range shared by Find dupes and the Lookbook."""

import numpy as np

from lookmate.catalog.index import VectorIndex
from lookmate.services.price_range import PriceRange, search_in_range


def test_range_defaults_to_the_budget_and_orders_its_ends():
    assert PriceRange.from_params(None, None, 30) == PriceRange(0, 45)
    assert PriceRange.from_params(80, 20, 30) == PriceRange(20, 80)


def test_widening_steps_and_labels():
    r = PriceRange(20, 40)
    assert r.steps() == [(20, 40), (10, 60), (5, 120), (None, None)]
    assert r.note(30) is None
    assert r.note(55) == "A bit above your price range"
    assert r.note(200).startswith("Above your price range")
    assert r.note(5) == "Below your price range"


def test_index_filters_by_minimum_price():
    idx = VectorIndex(["a", "b", "c"], np.eye(3, dtype=np.float32), ["top"] * 3, [5, 25, 90])
    q = np.ones(3, dtype=np.float32)
    assert {h.product_id for h in idx.search(q, k=5, min_price=10, max_price=50)} == {"b"}


def test_search_widens_only_as_far_as_needed(runtime):
    coats = sorted(p.price for p in runtime.catalog.products.values() if p.category == "outerwear")
    cheapest = coats[0]
    # Nothing at all under a tenth of the cheapest coat: the search still answers, from a wider step.
    res = search_in_range(runtime.catalog, "coat", 10, "outerwear", PriceRange(0, cheapest / 10))
    assert res, "never comes back empty"
    # A range that holds coats returns only those.
    res = search_in_range(runtime.catalog, "coat", 10, "outerwear", PriceRange(0, coats[-1]))
    assert all(r.product.price <= coats[-1] for r in res)


def test_a_finished_look_can_be_re_picked_for_another_range(client, runtime, user):
    from lookmate.worker import process_look

    png = b"\x89PNG\r\n\x1a\n" + b"range-look"
    look_id = client.post("/api/looks", data={"user_id": user["id"]},
                          files={"image": ("l.png", png, "image/png")}).json()["id"]
    assert process_look(runtime, int(runtime.queue.reserve(timeout_s=0.1))) == "done"
    calls = []
    runtime.llm.analyze_look = lambda *a: calls.append(a)  # re-picking must not call the model

    cheap = client.get(f"/api/looks/{look_id}", params={"price_min": 0, "price_max": 15}).json()["result"]
    assert cheap["price_range"] == {"low": 0, "high": 15} and not calls
    for section in cheap["sections"]:
        for p in section["picks"]:
            assert p["price"] <= 15 or any("price range" in r for r in p["reasons"])
