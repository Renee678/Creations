"""The image evaluation harness: matching, scoring and the results table. Runs offline with FakeVision."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from lookmate.eval import (Expected, dupes_precision, load_cases, match_pieces, percentile, run, score_attributes,
                           summarise, to_markdown)
from lookmate.llm.client import FakeVision
from lookmate.llm.schemas import DetectedItem

EVALS = Path(__file__).resolve().parents[1] / "evals"


def item(category, name, colour, details=(), partial=False):
    return DetectedItem(category=category, name=name, colour=colour, fit="", details=list(details), style_tags=[],
                        search_query=name, partial=partial)


def product(name, colour, category="bottom", product_type="", description=""):
    return SimpleNamespace(name=name, colour=colour, category=category, product_type=product_type,
                           description=description)


def test_cases_are_read_per_photo_with_blank_columns_unscored(tmp_path):
    f = tmp_path / "cases.csv"
    f.write_text("image,category,subtype,colour,length,pattern,partial,notes\n"
                 "a.jpg,bottom,skirt,Cream,midi,no,yes,first\n"
                 "a.jpg,shoes,,,,,,\n"
                 "# comment row,top,,,,,,\n"
                 "b.jpg,top,shirt,blue,,YES,,\n", encoding="utf-8")
    cases = load_cases(f)
    assert list(cases) == ["a.jpg", "b.jpg"]
    skirt, shoes = cases["a.jpg"]
    assert (skirt.subtype, skirt.colour, skirt.length, skirt.pattern, skirt.partial) == ("skirt", "cream", "midi", False, True)
    assert (shoes.subtype, shoes.colour, shoes.pattern, shoes.partial) == ("", "", None, None)
    assert cases["b.jpg"][0].pattern is True


def test_each_label_takes_the_best_detected_item_of_its_category_once():
    items = [item("bottom", "wide-leg trousers", "black"), item("bottom", "pleated midi skirt", "cream"),
             item("top", "silk blouse", "white")]
    labels = [Expected("a", "bottom", "skirt", "cream"), Expected("a", "bottom", "skirt", "cream"),
              Expected("a", "shoes")]
    pairs = match_pieces(labels, items)
    assert [i for _, i in pairs] == [1, 0, None], "the skirt goes to the skirt; a second label gets what's left"


def test_attributes_are_scored_with_the_search_rules():
    label = Expected("a", "bottom", "skirt", "ivory", "midi", pattern=False, partial=False)
    right = score_attributes(label, item("bottom", "pleated midi skirt", "cream"))
    assert right == {"category": True, "subtype": True, "colour": True, "length": True, "pattern": True, "cut_off": True}
    wrong = score_attributes(label, item("bottom", "floral maxi trousers", "red", partial=True))
    assert wrong == {"category": True, "subtype": False, "colour": False, "length": False, "pattern": False,
                     "cut_off": False}
    assert set(score_attributes(Expected("a", "shoes"), item("shoes", "loafers", "black"))) == {"category"}


def test_dupes_precision_checks_picks_against_the_label_not_the_reading():
    label = Expected("a", "bottom", "skirt", "black", "midi", pattern=False)
    picks = [product("Black midi skirt", "black"), product("Black mini skirt", "black"),
             product("Black midi skirt with floral print", "black"), product("Navy midi skirt", "navy"),
             product("Black midi skirt", "black"), product("Black midi skirt", "black")]
    assert dupes_precision(label, picks) == (2, 5), "only the top 5 count; wrong length, print or colour fail"
    assert dupes_precision(label, []) == (0, 0)


def test_percentiles_use_the_nearest_rank():
    assert percentile([], 95) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile(list(range(1, 21)), 95) == 19
    assert percentile(list(range(1, 101)), 50) == 50


def test_the_summary_counts_misses_zero_results_and_latency():
    from lookmate.eval import CaseResult

    a = CaseResult("a", [Expected("a", "top", "shirt"), Expected("a", "bag")],
                   detected=[item("top", "blouse", "white"), item("shoes", "flats", "black")],
                   pairs=[(Expected("a", "top", "shirt"), 0), (Expected("a", "bag"), None)],
                   attributes=[{"category": True, "subtype": True}], precision=[(0, 0)],
                   perception_ms=1000, dupes_ms=200)
    b = CaseResult("b", [Expected("b", "top")], error="perception failed: boom")
    s = summarise([a, b])
    assert s["category_recall"] == pytest.approx(1 / 3) and s["accuracy"]["subtype"] == (1.0, 1)
    assert s["zero_result_rate"] == 1.0 and s["dupes_precision"] is None and s["extra_items"] == 1
    assert s["errors"] == 1 and s["latency_median_ms"] == 1200
    md = to_markdown([a, b], s, "fake")
    assert "| Category recall (labelled pieces found) | 33% | 3 |" in md
    assert "missed bag" in md and "perception failed: boom" in md


def test_the_bundled_examples_run_offline(runtime):
    from lookmate.services.dupes import find_dupes
    from lookmate.services.ranking import UserContext

    results, summary = run(EVALS / "cases.csv", EVALS / "photos", FakeVision(),
                           lambda analysis: find_dupes(analysis, runtime.catalog, UserContext()))
    assert summary["images"] == 2 and summary["errors"] == 0
    assert summary["expected"] == 6 and summary["matched"] == 5, "the example bag is deliberately missing"
    assert all(acc == 1.0 for acc, _ in summary["accuracy"].values()), "the labels describe what FakeVision returns"
    assert summary["latency_median_ms"] is not None
    md = to_markdown(results, summary, "fake")
    assert md.startswith("## Image evaluation (2 photos, 6 labelled pieces, perception: fake)")


def test_photos_are_sent_as_the_app_sends_them(tmp_path):
    """The browser shrinks photos to 1024 px JPEG before upload; a raw phone photo can exceed the API's 5 MB."""
    from io import BytesIO

    from PIL import Image

    from lookmate.eval import load_photo

    big = tmp_path / "phone.png"
    Image.new("RGB", (3000, 4000), "pink").save(big)
    data, media_type = load_photo(big)
    assert media_type == "image/jpeg" and max(Image.open(BytesIO(data)).size) == 1024
    junk = tmp_path / "junk.webp"
    junk.write_bytes(b"not an image")
    assert load_photo(junk) == (b"not an image", "image/webp"), "the model gets to say it can't read it"


def test_price_in_range_counts_every_shown_dupe_and_names_the_ones_outside():
    """Renee (2026-10-06): prove the hard price filter: the share of shown dupes inside the run's range."""
    from lookmate.eval import CaseResult, evaluate_case
    from lookmate.llm.schemas import LookAnalysis

    pick = lambda name, price: {"name": name, "price": price, "id": name, "category": "top", "colour": "white",  # noqa: E731
                                "product_type": "", "description": ""}
    llm = SimpleNamespace(analyze_look=lambda *a: LookAnalysis(
        is_outfit=True, vibe="", style_tags=[], items=[item("top", "shirt", "white"), item("shoes", "flats", "black", partial=True)]))
    result = {"price_range": {"low": 0, "high": 85}, "sections": [
        {"item": {}, "hidden": False, "picks": [pick("Cheap shirt", 20.0), pick("Pricey jeans", 190.0), pick("Edge", 85.0)]},
        {"item": {}, "hidden": True, "picks": [pick("Hidden flats", 300.0)]},  # not shown, not counted
    ]}
    res = evaluate_case("a.jpg", [Expected("a.jpg", "top", "shirt")], b"x", "image/jpeg", llm, lambda a: result)
    assert res.prices == [("Cheap shirt", 20.0, True), ("Pricey jeans", 190.0, False), ("Edge", 85.0, True)]

    clean = CaseResult("b.jpg", [], prices=[("Fine", 10.0, True)], price_range={"low": 0, "high": 85})
    s = summarise([res, clean])
    assert s["price_in_range"] == pytest.approx(3 / 4) and s["price_checked"] == 4
    assert s["out_of_range"] == [("a.jpg", "Pricey jeans", 190.0, {"low": 0, "high": 85})]
    md = to_markdown([res, clean], s, "fake")
    assert "| Price in range (shown dupes inside the run's price range) | 75% | 4 picks |" in md
    assert "| 2/3 |" in md and "| 1/1 |" in md, "per photo"
    assert "- a.jpg: Pricey jeans, $190.00 (range $0–$85)" in md
    assert "### Picks outside the price range\n\nNone." in to_markdown([clean], summarise([clean]), "fake")


def test_the_real_search_keeps_every_shown_dupe_in_range(runtime):
    from lookmate.services.dupes import find_dupes
    from lookmate.services.price_range import PriceRange
    from lookmate.services.ranking import UserContext

    for high in (15, 40, 50):
        _, summary = run(EVALS / "cases.csv", EVALS / "photos", FakeVision(),
                         lambda analysis: find_dupes(analysis, runtime.catalog, UserContext(), PriceRange(0, high)))
        assert summary["out_of_range"] == [] and summary["price_in_range"] in (1.0, None), high
