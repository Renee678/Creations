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


def test_free_text_categories_follow_label_then_name():
    from lookmate.catalog.categories import category_from_text

    assert category_from_text("Coats & Jackets", "New Look trench coat in camel") == ("outerwear", "coats")
    assert category_from_text("", "ASOS DESIGN shirt dress in white")[0] == "dress"
    assert category_from_text("Day Dresses", "tibi knit long sleeve dress")[0] == "dress"
    assert category_from_text("Boots", "leather over-the-knee boots")[0] == "shoes"
    assert category_from_text("Handbags", "phillip lim red leather satchel")[0] == "bag"
    assert category_from_text("Backpacks", "madden girl polka dot backpack")[0] == "bag"
    assert category_from_text("Earrings", "ceramic heart stud earrings")[0] == "accessory"
    # Not recommended: menswear, swimwear, homeware, unknown things.
    assert category_from_text("Men's Shirts", "contrast trimmed cotton shirt") is None
    assert category_from_text("", "Monki bikini top in black") is None
    assert category_from_text("Dining Chairs", "gray mary jane dining chair") is None


def test_designer_prices_sit_well_above_budget_prices():
    budget = [synthetic_price(f"pv-{i}", "dress") for i in range(100)]
    designer = [synthetic_price(f"pv-{i}", "dress", designer=True) for i in range(100)]
    assert min(designer) > max(budget)
    assert all(p % 10 == 0 for p in designer)


ASOS_HEADER = ["url", "name", "size", "category", "price", "color", "sku", "description", "images"]


def _asos_row(name, sku, price="49.99", color="Neutral", label="Coats & Jackets",
              image="https://images.asos-media.com/products/x/1-4?$n_1920w$&wid=1926&fit=constrain"):
    details = f"{label} by New LookLow-key layeringNotch collarProduct Code: {sku}"
    return ["https://www.asos.com/x", name, "UK 6,UK 8", name, price, color, f"{sku}.0",
            str([{"Product Details": details}, {"Brand": "Since the 60s"}]), str([image, image + "&x=2"])]


def _asos_csv(path):
    import csv

    rows = [
        _asos_row("New Look trench coat in camel", "126704571"),
        _asos_row("New Look trench coat in camel", "126704571"),  # same product, another size row
        _asos_row("ASOS DESIGN satin slip dress in sage green", "200001", "32.00", "Green", "Dresses"),
        _asos_row("Monki bikini top in black", "200002", label="Swimwear"),
        _asos_row("ASOS DESIGN mug", "200003", label="Home"),
        _asos_row("Missing photo dress", "200004", label="Dresses", image=""),
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(ASOS_HEADER)
        w.writerows(rows)


def test_asos_import_dedupes_sizes_and_keeps_real_prices_and_photos(tmp_path):
    from lookmate.catalog.importer import load_asos_rows
    from lookmate.catalog.pricing import GBP_TO_USD

    _asos_csv(tmp_path / "asos.csv")
    rows = {r["id"]: r for r in load_asos_rows(tmp_path / "asos.csv", size=10)}
    assert set(rows) == {"asos-126704571", "asos-200001"}

    coat = rows["asos-126704571"]
    assert coat["category"] == "outerwear" and coat["product_type"] == "Coats & Jackets"
    assert coat["colour"] == "camel", "the colour in the name beats the broad 'Neutral'"
    assert coat["price"] == round(49.99 * GBP_TO_USD, 2)
    assert coat["image_url"].startswith("https://images.asos-media.com/") and "&x=2" not in coat["image_url"]
    assert "Low-key layering" in coat["description"] and "Product Code" not in coat["description"]
    assert rows["asos-200001"]["category"] == "dress" and rows["asos-200001"]["colour"] == "sage green"
    assert len(load_asos_rows(tmp_path / "asos.csv", size=1)) == 1


JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
PNG = b"\x89PNG\r\n\x1a\nfake-png"


def _polyvore_parquet(path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    items = [
        ("100_1", "Day Dresses", "tibi knit long sleeve dress black", JPEG),
        ("100_2", "Boots", "michael kors leather over-the-knee boots", PNG),
        ("100_3", "Floral Decor", "pier imports stem", JPEG),
        ("100_4", "Men's Shirts", "contrast trimmed cotton shirt", JPEG),
        ("200_1", "Day Dresses", "tibi knit long sleeve dress black", JPEG),  # same piece, another outfit
        ("200_2", "Handbags", "phillip lim red leather satchel", None),     # no photo
    ]
    pq.write_table(pa.table({
        "image": [{"bytes": img, "path": None} if img else None for *_, img in items],
        "category": [c for _, c, _, _ in items],
        "text": [t for _, _, t, _ in items],
        "item_ID": [i for i, *_ in items],
    }), path)


def test_polyvore_import_saves_photos_and_prices_designer_pieces(tmp_path):
    from lookmate.catalog.importer import load_polyvore_rows

    _polyvore_parquet(tmp_path / "pv.parquet")
    image_dir = tmp_path / "images"
    rows = {r["id"]: r for r in load_polyvore_rows(tmp_path / "pv.parquet", 10, image_dir)}
    assert set(rows) == {"pv-100_1", "pv-100_2"}, "decor, menswear, repeats and photo-less items are skipped"

    dress, boots = rows["pv-100_1"], rows["pv-100_2"]
    assert dress["name"] == "Tibi knit long sleeve dress black" and dress["colour"] == "black"
    assert dress["image_url"] == "/catalog-images/polyvore/100_1.jpg"
    assert (image_dir / "100_1.jpg").read_bytes() == JPEG and (image_dir / "100_2.png").read_bytes() == PNG
    assert boots["category"] == "shoes" and boots["price"] >= 100

    again = load_polyvore_rows(tmp_path / "pv.parquet", 10, image_dir)
    assert {r["id"] for r in again} == set(rows), "re-running the import is harmless"


def test_mixed_catalog_imports_both_sources_and_keeps_them(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from lookmate.catalog import importer
    from lookmate.db import Base
    from lookmate.models import Product

    _asos_csv(tmp_path / "asos.csv")
    _polyvore_parquet(tmp_path / "pv.parquet")
    monkeypatch.setattr(importer, "download_asos", lambda cache: tmp_path / "asos.csv")
    monkeypatch.setattr(importer, "download_polyvore", lambda cache: tmp_path / "pv.parquet")
    eng = create_engine(f"sqlite:///{tmp_path}/cat.db")
    Base.metadata.create_all(eng)
    data_dir = tmp_path / "data"
    seed = Path(__file__).resolve().parents[1] / "data" / "seed_products.json"
    data_dir.mkdir()
    (data_dir / "seed_products.json").write_text(seed.read_text())

    with Session(eng) as s:
        assert importer.ensure_catalog(s, HashEmbedder(), "asos, polyvore", data_dir, 50) == 4
        assert importer.stored_sources(s) == {"asos", "polyvore"}
        assert (data_dir / "cache" / "images" / "polyvore" / "100_1.jpg").exists()
        assert importer.ensure_catalog(s, HashEmbedder(), "polyvore,asos", data_dir, 50) == 4, "same mix: kept"

        importer.ensure_catalog(s, HashEmbedder(), "seed", data_dir, 50)
        assert importer.stored_sources(s) == {"seed"}
        assert all(i.startswith("seed-") for i in s.scalars(select(Product.id)))


def test_failed_downloads_fall_back_to_the_seed_catalog(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from lookmate.catalog import importer
    from lookmate.db import Base

    def offline(cache):
        raise OSError("no network")

    monkeypatch.setattr(importer, "download_asos", offline)
    monkeypatch.setattr(importer, "download_polyvore", offline)
    eng = create_engine(f"sqlite:///{tmp_path}/cat.db")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        assert importer.ensure_catalog(s, HashEmbedder(), "asos,polyvore",
                                       Path(__file__).resolve().parents[1] / "data", 50) > 0
        assert importer.stored_sources(s) == {"seed"}


def test_unknown_catalog_source_is_rejected():
    from lookmate.catalog.importer import parse_sources

    assert parse_sources("ASOS, polyvore") == {"asos", "polyvore"}
    with pytest.raises(ValueError):
        parse_sources("asos,zara")


AMAZON_SAMPLE = Path(__file__).parent / "fixtures" / "amazon_meta_sample.jsonl"


def test_amazon_keeps_womens_apparel_shoes_and_bags_with_a_price_and_photo(tmp_path):
    from lookmate.catalog.importer import load_amazon_rows

    lines = AMAZON_SAMPLE.read_bytes().splitlines()
    rows = load_amazon_rows(tmp_path, 100, lines=lines)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"amz-B0DRESS1", "amz-B0SKIRT1", "amz-B0BOOTS1", "amz-B0TOTE01"}, \
        "no menswear, kids, jewellery, lingerie, costumes, or pieces without a price or photo; no duplicates"
    dress = by_id["amz-B0DRESS1"]
    assert dress["category"] == "dress" and dress["price"] == 39.99 and dress["colour"] == "Black"
    assert dress["name"] == "Long Sleeve Wrap Midi Dress", "no brand, 'Womens' or 'Fall' in the name"
    assert dress["image_url"].startswith("https://m.media-amazon.com/") and "Dokotoo" in dress["section"]
    assert by_id["amz-B0SKIRT1"]["name"].startswith("Satin Maxi Skirt")
    assert by_id["amz-B0BOOTS1"]["category"] == "shoes" and by_id["amz-B0TOTE01"]["category"] == "bag"
    assert by_id["amz-B0TOTE01"]["colour"] == "", "'As Shown' is not a colour"

    # The filtered list is cached: a second import (a redeploy) doesn't stream the file again.
    assert load_amazon_rows(tmp_path, 100, lines=iter(())) == rows


def test_amazon_products_link_to_their_listing():
    from lookmate.catalog.service import ProductView

    links = ProductView("amz-B0DRESS1", "Wrap midi dress", "Dresses", "dress", "Black", "", "", 39.99).shop_links()
    assert links["amazon"] == "https://www.amazon.com/dp/B0DRESS1" and "shein" in links


def test_embeddings_are_cached_in_chunks_so_an_import_resumes(tmp_path, monkeypatch):
    from lookmate.catalog import importer
    from lookmate.catalog.embedder import HashEmbedder

    class Counting(HashEmbedder):
        calls = 0

        def embed_documents(self, texts):
            Counting.calls += len(texts)
            return super().embed_documents(texts)

    monkeypatch.setattr(importer, "EMBED_CHUNK", 2)
    rows = [{"name": f"Knit top {i}", "product_type": "Tops", "colour": "Grey"} for i in range(5)]
    first = importer._embed(rows, Counting(), tmp_path)
    assert Counting.calls == 5 and len(list((tmp_path / "embeddings").glob("*.npy"))) == 3
    again = importer._embed(rows, Counting(), tmp_path)
    assert Counting.calls == 5, "nothing re-embedded"
    assert (first == again).all()
