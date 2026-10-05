"""Personal analysis (photos -> queue -> worker) and the deterministic lookbook built from it."""

import pytest

from lookmate.llm.client import FakeVision, LLMTransientError
from lookmate.llm.schemas import Swatch
from lookmate.services.colours import family, palette_score
from lookmate.services.lookbook import OCCASIONS, SEASONS, Palette
from lookmate.worker import process_job

SELFIE = b"\x89PNG\r\n\x1a\n" + b"selfie"
FULL_BODY = b"\x89PNG\r\n\x1a\n" + b"full-body"


def upload(client, user_id, *photos, ctype="image/png"):
    files = [("photos", (f"p{i}.png", data, ctype)) for i, data in enumerate(photos)]
    return client.post(f"/api/users/{user_id}/analyses", files=files)


def run_next_job(runtime):
    job = runtime.queue.reserve(timeout_s=0.1)
    assert job is not None, "expected a queued job"
    return process_job(runtime, job)


def test_photos_become_a_personal_analysis(client, runtime, user):
    res = upload(client, user["id"], SELFIE, FULL_BODY)
    assert res.status_code == 202 and res.json()["status"] == "queued"

    assert run_next_job(runtime) == "done"
    result = client.get(f"/api/analyses/{res.json()['id']}").json()["result"]
    assert result["colour_season"] in SEASONS
    assert len(result["best_colours"]) == 8 and all(s["hex"].startswith("#") for s in result["best_colours"])
    assert result["hair_suggestions"] and result["makeup_suggestions"]
    assert runtime.queue.depth() == {"ready": 0, "processing": 0, "delayed": 0}


def test_photos_are_deleted_after_analysis(client, runtime, user):
    from lookmate.db import SessionLocal
    from lookmate.models import PersonalAnalysis

    analysis_id = upload(client, user["id"], SELFIE).json()["id"]
    run_next_job(runtime)
    with SessionLocal() as s:
        assert s.get(PersonalAnalysis, analysis_id).photos is None


def test_same_photos_are_deduplicated(client, runtime, user):
    first = upload(client, user["id"], SELFIE, FULL_BODY)
    second = upload(client, user["id"], SELFIE, FULL_BODY)
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    assert runtime.queue.depth()["ready"] == 1


def test_photo_upload_validation(client, user):
    assert upload(client, user["id"]).status_code == 422  # no photos at all
    assert upload(client, user["id"], SELFIE, SELFIE, SELFIE, SELFIE).status_code == 400
    assert upload(client, user["id"], SELFIE, ctype="application/pdf").status_code == 415
    assert upload(client, 424242, SELFIE).status_code == 404


def test_transient_failure_is_retried_then_succeeds(client, runtime, user, monkeypatch):
    calls = {"n": 0}
    real = FakeVision().analyze_person

    def flaky(photos):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMTransientError("overloaded")
        return real(photos)

    monkeypatch.setattr(runtime.llm, "analyze_person", flaky)
    analysis_id = upload(client, user["id"], SELFIE).json()["id"]
    assert run_next_job(runtime) == "queued"
    assert runtime.queue.promote_due(now=1e12) == 1
    assert run_next_job(runtime) == "done"
    assert client.get(f"/api/analyses/{analysis_id}").json()["attempts"] == 2


def test_photo_without_a_face_fails_with_a_clear_message(client, runtime, user, monkeypatch):
    def no_face(photos):
        return FakeVision().analyze_person(photos).model_copy(update={"usable": False})

    monkeypatch.setattr(runtime.llm, "analyze_person", no_face)
    analysis_id = upload(client, user["id"], SELFIE).json()["id"]
    assert run_next_job(runtime) == "failed"
    assert "face" in client.get(f"/api/analyses/{analysis_id}").json()["error"]


@pytest.mark.parametrize("mode, sections", [("seasons", SEASONS), ("occasions", OCCASIONS)])
def test_lookbook_has_complete_outfits_without_repeats(client, runtime, user, mode, sections):
    upload(client, user["id"], SELFIE)
    run_next_job(runtime)
    lb = client.get(f"/api/users/{user['id']}/lookbook", params={"mode": mode}).json()

    assert lb["personal"] is True and lb["analysis"]["colour_season"] in SEASONS
    assert [s["id"] for s in lb["sections"]] == list(sections)
    ids = []
    for section in lb["sections"]:
        assert section["outfits"], f"no outfits for {section['id']}"
        for outfit in section["outfits"]:
            cats = [p["category"] for p in outfit["pieces"]]
            assert len(cats) == len(set(cats)), "an outfit never has two pieces of the same category"
            assert outfit["total_price"] == pytest.approx(sum(p["price"] for p in outfit["pieces"]))
            ids += [p["id"] for p in outfit["pieces"]]
    assert len(ids) / len(set(ids)) < 1.5, "pieces are mostly distinct across the lookbook"


def test_lookbook_works_before_any_photo(client, user):
    lb = client.get(f"/api/users/{user['id']}/lookbook").json()
    assert lb["personal"] is False and lb["analysis"] is None
    assert lb["styles"][0]["id"] == "old_money", "the user's declared style leads"
    assert all(s["outfits"] for s in lb["sections"])


def test_lookbook_prefers_the_users_palette(runtime):
    from lookmate.services.lookbook import build_lookbook
    from lookmate.services.ranking import UserContext

    class Rec:
        result = {"best_colours": [{"name": "light pink", "hex": "#f7c6c7"}, {"name": "beige", "hex": "#d8c3a5"}],
                  "avoid_colours": [{"name": "black", "hex": "#111111"}], "style_tags": []}

    lb = build_lookbook(runtime.catalog, UserContext(), Rec, [], "seasons")
    colours = [family(p["colour"]) for s in lb["sections"] for o in s["outfits"] for p in o["pieces"]]
    assert colours.count("pink") + colours.count("beige") > len(colours) / 2


def test_colour_families_bridge_model_and_catalog_names():
    assert family("Dark Blue") == "navy" and family("navy") == "navy"
    assert family("dusty rose") == family("Light Pink") == "pink"
    assert family("Greyish Beige") == family("camel") == "beige"
    assert family("Off White") == "cream" and family("Multicoloured") is None
    assert palette_score("Black", {"pink"}, {"black"}) == -1.0
    assert palette_score("Light Pink", {"pink"}, {"black"}) == 1.0


def test_palette_puts_an_accent_first_and_neutrals_after():
    p = Palette.from_analysis({"best_colours": [{"name": "emerald"}, {"name": "navy"}, {"name": "white"}],
                               "avoid_colours": [{"name": "orange"}]})
    assert p.good == {"green", "navy", "white"} and p.bad == {"orange"}
    colours = p.colours_for(0, 3)
    assert colours[0] == "emerald" and set(colours[1:]) <= {"navy", "white"}


def test_bad_hex_from_the_model_is_replaced():
    assert Swatch(name="x", hex="zzz").hex == "#999999"
    assert Swatch(name="x", hex="C19A6B").hex == "#c19a6b"


def test_lookbook_pieces_stay_in_the_price_range_when_they_can(runtime):
    from lookmate.services.lookbook import Palette, _fill_slot
    from lookmate.services.price_range import PriceRange
    from lookmate.services.ranking import UserContext

    for low, high in [(0, 15), (0, 30), (20, 60)]:
        price = PriceRange(low, high)
        for category in ("top", "bottom", "dress", "outerwear", "shoes", "bag"):
            fits = [p for p in runtime.catalog.products.values() if p.category == category and low <= p.price <= high]
            piece = _fill_slot(runtime.catalog, UserContext(), Palette.from_analysis(None), "minimalist",
                               category, category, "black", set(), price)
            if fits:
                assert low <= piece["price"] <= high, (low, high, category, piece["name"])
                assert "In your price range" in piece["reasons"]
            elif piece is None:
                near = [p for p in runtime.catalog.products.values() if p.category == category and p.price <= high * 3]
                assert not near, "a slot is only left out when nothing is within 3x the range"
            else:
                assert piece["price"] <= high * 3, "never a wildly overpriced piece in an outfit"
                assert any("price range" in r for r in piece["reasons"]), "a widened pick says so"


def test_an_outfit_leaves_out_a_piece_rather_than_blow_the_budget(runtime):
    from lookmate.services.lookbook import Palette, _fill_slot
    from lookmate.services.price_range import PriceRange
    from lookmate.services.ranking import UserContext

    cheapest = min(p.price for p in runtime.catalog.products.values() if p.category == "shoes")
    piece = _fill_slot(runtime.catalog, UserContext(), Palette.from_analysis(None), "minimalist",
                       "shoes", "shoes", "black", set(), PriceRange(0, cheapest / 4))
    assert piece is None


def test_lookbook_takes_a_price_range(client, user):
    lb = client.get(f"/api/users/{user['id']}/lookbook", params={"price_min": 0, "price_max": 20}).json()
    assert lb["price_range"] == {"low": 0, "high": 20}
    default = client.get(f"/api/users/{user['id']}/lookbook").json()
    assert default["price_range"] == {"low": 0, "high": 45}, "no range chosen: up to 1.5x the $30 budget"


def test_photo_checks_say_which_photo_is_for_try_on(client, runtime, user):
    res = upload(client, user["id"], SELFIE, FULL_BODY)
    run_next_job(runtime)
    checks = client.get(f"/api/analyses/{res.json()['id']}").json()["result"]["photo_checks"]
    assert [c["framing"] for c in checks] == ["face", "full_body"]
    assert [c["good_for_tryon"] for c in checks] == [False, True]
