import numpy as np
import pytest

from stylebuddy.catalog.categories import category_for
from stylebuddy.catalog.embedder import HashEmbedder
from stylebuddy.catalog.index import VectorIndex
from stylebuddy.catalog.pricing import synthetic_price


def test_hash_embedder_is_normalised_and_deterministic():
    e = HashEmbedder()
    a, b = e.embed_documents(["beige knit cardigan", "beige knit cardigan"])
    assert np.isclose(np.linalg.norm(a), 1.0)
    assert np.array_equal(a, b)


def test_hash_embedder_ranks_lexical_overlap_higher():
    e = HashEmbedder()
    q = e.embed_query("linen maxi dress for the beach")
    near, far = e.embed_documents(["Sleeveless linen maxi dress, beach resort", "Leather biker jacket"])
    assert q @ near > q @ far


def _index():
    vecs = np.eye(4, dtype=np.float32)
    return VectorIndex(["a", "b", "c", "d"], vecs, ["top", "top", "dress", "top"], [10, 30, 15, 5])


def test_index_returns_best_match_first():
    hits = _index().search(np.array([0, 1, 0, 0.5], dtype=np.float32), k=2)
    assert [h.product_id for h in hits] == ["b", "d"]


def test_index_applies_category_price_and_exclude_filters():
    idx = _index()
    q = np.array([1, 1, 1, 1], dtype=np.float32)
    assert {h.product_id for h in idx.search(q, k=10, category="top", max_price=12)} == {"a", "d"}
    assert {h.product_id for h in idx.search(q, k=10, exclude={"a", "b"})} == {"c", "d"}
    assert idx.search(q, k=3, category="shoes") == []


def test_index_rejects_mismatched_input():
    with pytest.raises(ValueError):
        VectorIndex(["a"], np.zeros((2, 3)), ["top"], [1])


def test_synthetic_price_is_stable_and_category_scaled():
    assert synthetic_price("123", "top") == synthetic_price("123", "top")
    assert str(synthetic_price("123", "top")).endswith(".99")
    tops = [synthetic_price(str(i), "top") for i in range(200)]
    coats = [synthetic_price(str(i), "outerwear") for i in range(200)]
    assert np.mean(coats) > np.mean(tops)


def test_category_mapping_skips_unsupported_types():
    assert category_for("Cardigan") == "top"
    assert category_for("Underwear bottom") is None
