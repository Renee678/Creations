from pathlib import Path

import numpy as np
import pytest

from lookmate.catalog.categories import category_for
from lookmate.catalog.embedder import HashEmbedder
from lookmate.catalog.index import VectorIndex
from lookmate.catalog.pricing import synthetic_price


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


def _hm_parquet(path, n=6, vector_col="dense_embedding"):
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = {
        "article_id": [100 + i for i in range(n)],
        "prod_name": [f"Item {i}" for i in range(n)],
        "product_type_name": ["Dress", "Socks", "Trousers", "Dress", "Top", "Coat"][:n],
        "colour_group_name": ["Black"] * n,
        "graphical_appearance_name": ["Solid"] * n,
        "index_group_name": ["Ladieswear", "Ladieswear", "Baby/Children", "Divided", "Menswear", "Ladieswear"][:n],
        "section_name": ["Womens"] * n,
        "detail_desc": ["Soft and easy."] * (n - 1) + [None],
        "image_url": [f"https://img/{i}.jpg" for i in range(n)],
        vector_col: [[float(i)] * 4 for i in range(n)],
    }
    pq.write_table(pa.table(rows), path)


def test_hm_import_filters_then_fetches_vectors_for_the_sample_only(tmp_path):
    from lookmate.catalog.importer import load_hm_rows

    _hm_parquet(tmp_path / "hm.parquet")
    rows = load_hm_rows(tmp_path / "hm.parquet", size=10)
    # Socks (unsupported type), children's wear and the item without a description are dropped.
    assert sorted(r["id"] for r in rows) == ["100", "103", "104"]
    for r in rows:
        assert r["vector"] == [float(int(r["id"]) - 100)] * 4, "each row keeps its own embedding"
        assert r["image_url"].startswith("https://")
    assert len(load_hm_rows(tmp_path / "hm.parquet", size=2)) == 2


def test_catalog_is_reimported_when_the_source_changes(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from lookmate.catalog import importer
    from lookmate.catalog.embedder import HashEmbedder
    from lookmate.db import Base
    from lookmate.models import Product

    eng = create_engine(f"sqlite:///{tmp_path}/cat.db")
    Base.metadata.create_all(eng)
    data_dir = Path(__file__).resolve().parents[1] / "data"
    with Session(eng) as s:
        importer.ensure_catalog(s, HashEmbedder(), "seed", data_dir, 50)
        assert s.scalar(select(Product.id).limit(1)).startswith("seed-")

        _hm_parquet(tmp_path / "hm.parquet")
        monkeypatch.setattr(importer, "download_hm", lambda cache_dir: tmp_path / "hm.parquet")
        assert importer.ensure_catalog(s, HashEmbedder(), "hm", data_dir, 50) == 3
        assert not any(i.startswith("seed-") for i in s.scalars(select(Product.id)))
        assert importer.ensure_catalog(s, HashEmbedder(), "hm", data_dir, 50) == 3, "same source: kept as is"


def test_hm_import_accepts_the_older_vector_column_name(tmp_path):
    from lookmate.catalog.importer import load_hm_rows

    _hm_parquet(tmp_path / "hm.parquet", vector_col="bge_embedding")
    assert all(r["vector"] is not None for r in load_hm_rows(tmp_path / "hm.parquet", size=10))
