"""Personal analysis (photos -> queue -> worker) and the deterministic lookbook built from it."""

from datetime import datetime, timezone

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
    url = f"/api/users/{user['id']}/lookbook"
    if mode == "seasons":  # one season per page
        pages = [client.get(url, params={"mode": mode, "season": s}).json() for s in sections]
    else:
        pages = [client.get(url, params={"mode": mode}).json()]
    lb = pages[0]

    assert lb["personal"] is True and lb["analysis"]["colour_season"] in SEASONS
    assert [s["id"] for page in pages for s in page["sections"]] == list(sections)
    for page in pages:
        ids = []
        for section in page["sections"]:
            assert section["outfits"], f"no outfits for {section['id']}"
            for outfit in section["outfits"]:
                cats = [p["category"] for p in outfit["pieces"]]
                assert len(cats) == len(set(cats)), "an outfit never has two pieces of the same category"
                assert outfit["total_price"] == pytest.approx(sum(p["price"] for p in outfit["pieces"]))
                ids += [p["id"] for p in outfit["pieces"]]
        assert len(ids) / len(set(ids)) < 1.5, "pieces are mostly distinct across a page"


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


def test_my_style_carries_the_colour_report(client, runtime, user):
    assert client.get(f"/api/users/{user['id']}/style").json()["analysis"] is None
    upload(client, user["id"], SELFIE, FULL_BODY)
    run_next_job(runtime)
    report = client.get(f"/api/users/{user['id']}/style").json()["analysis"]
    assert report["colour_season"] and report["best_colours"], "My Style shows the colour season and palette"


def test_trends_say_which_suit_you_once_you_have_an_analysis(client, runtime, user):
    from lookmate.db import SessionLocal
    from lookmate.services import trends

    with SessionLocal() as s:
        trends.refresh(s, None, runtime.data_dir)
    upload(client, user["id"], SELFIE, FULL_BODY)
    run_next_job(runtime)
    body = client.get("/api/trends", params={"user_id": user["id"]}).json()
    verdicts = {t["fit"]["verdict"] for t in body["trends"]}
    assert verdicts <= {"suits", "adapt", "neutral"} and "suits" in verdicts


def test_my_style_explains_the_fit_rules(client, user):
    fit = client.get(f"/api/users/{user['id']}/style").json()["fit"]
    assert fit["shape"] == "Pear" and "wide" in fit["look_for"] and "skinny" in fit["skip"]


def test_lookbook_shows_the_current_season_and_offers_the_next(client, user, monkeypatch):
    from lookmate.services.trends import season_of

    now = season_of(datetime.now(timezone.utc).month)
    lb = client.get(f"/api/users/{user['id']}/lookbook").json()
    assert [s["id"] for s in lb["sections"]] == [now] and lb["season"] == now == lb["current_season"]
    assert lb["next_season"] != now
    nxt = client.get(f"/api/users/{user['id']}/lookbook", params={"season": lb["next_season"]}).json()
    assert [s["id"] for s in nxt["sections"]] == [lb["next_season"]]


WINTER = {"best_colours": [{"name": "emerald", "hex": "#00805a"}, {"name": "navy", "hex": "#1b2a4a"},
                           {"name": "white", "hex": "#ffffff"}, {"name": "cobalt blue", "hex": "#0047ab"},
                           {"name": "black", "hex": "#000000"}],
          "avoid_colours": [{"name": "camel", "hex": "#c19a6b"}], "style_tags": ["quiet_luxury"]}


def test_an_outfit_has_one_accent_and_true_neutrals(runtime):
    """The bug: a 'navy' slot matched bright blue trousers, which next to a green blazer made two loud colours."""
    from lookmate.services.lookbook import CORE_NEUTRALS, LOUD, build_lookbook
    from lookmate.services.ranking import UserContext

    class Rec:
        result = WINTER

    for mode in ("seasons", "occasions"):
        for season in ("spring", "autumn", "winter"):
            lb = build_lookbook(runtime.catalog, UserContext(style_weights={"quiet_luxury": 1.0}), Rec, [], mode,
                                season=season)
            for o in (o for s in lb["sections"] for o in s["outfits"]):
                for p in o["pieces"][1:]:
                    fam = family(p["colour"]) if p["colour"] else None
                    assert fam in CORE_NEUTRALS, (o["title"], p["name"], p["colour"])
                    assert not LOUD.search(p["name"]), (o["title"], p["name"])
                assert family(o["pieces"][0]["colour"] or "") != "beige", "colours to avoid stay out"


def test_neutral_slots_reject_bright_pieces_even_in_palette_families(runtime):
    from lookmate.services.lookbook import Palette, _colour_ok

    class P:
        def __init__(self, name, colour):
            self.name, self.colour = name, colour

    pal = Palette.from_analysis(WINTER)
    assert "blue" in pal.good, "cobalt is in this palette..."
    assert not _colour_ok(P("Faux leather trousers", "Bright Blue"), pal, "quiet_luxury", neutral=True), \
        "...but not for a neutral slot"
    assert _colour_ok(P("Tailored trousers", "Navy"), pal, "quiet_luxury", neutral=True)
    assert not _colour_ok(P("Neon slip skirt", "Black"), pal, "minimalist", neutral=True)
    assert not _colour_ok(P("Metallic blazer", "Green"), pal, "quiet_luxury", neutral=False), "muted styles stay muted"
    assert _colour_ok(P("Metallic blazer", "Green"), pal, "y2k", neutral=False)
    assert not _colour_ok(P("Wool coat", "Camel"), pal, "old_money", neutral=True), "camel is on the avoid list"
    warm = Palette.from_analysis({"best_colours": [{"name": "camel"}, {"name": "rust"}], "avoid_colours": []})
    assert _colour_ok(P("Wool coat", "Camel"), warm, "old_money", neutral=True), "warm palettes keep camel"


def test_the_stylist_chooses_among_the_top_candidates(runtime):
    from lookmate.services.lookbook import build_lookbook
    from lookmate.services.ranking import UserContext

    class Rec:
        result = WINTER

    seen = []

    def stylist(req):
        seen.append(req)
        o = req.outfits[0]
        assert all(1 <= len(s.candidates) <= 5 for s in o.slots), "a short list, not the whole catalog"
        assert req.outfits[0].style_definition, "the stylist sees what the style means"
        picks = [s.candidates[-1].id for s in o.slots]
        picks[0] = "not-a-candidate"  # invented ids are ignored
        return {0: (picks, "Tonal and calm.", True)}

    plain = build_lookbook(runtime.catalog, UserContext(), Rec, [], "seasons", season="autumn")
    styled = build_lookbook(runtime.catalog, UserContext(), Rec, [], "seasons", season="autumn", stylist=stylist)
    assert len(seen) == 1, "one call for the whole page"
    first, base = styled["sections"][0]["outfits"][0], plain["sections"][0]["outfits"][0]
    assert first["why"] == "Tonal and calm." and styled["styled"] and not plain["styled"]
    assert first["pieces"][0]["id"] == base["pieces"][0]["id"], "an invented id keeps the scorer's pick"
    expected = [s.candidates[-1].id for s in seen[0].outfits[0].slots][1:]
    assert [p["id"] for p in first["pieces"][1:]] == expected
    assert first["total_price"] == pytest.approx(sum(p["price"] for p in first["pieces"]))


def test_stylist_falls_back_and_caches(runtime):
    from lookmate.llm.client import FakeVision, LLMTransientError
    from lookmate.llm.schemas import StylingRequest, StylistCandidate, StylistOutfit, StylistSlot
    from lookmate.services import stylist

    req = StylingRequest(setting="autumn", palette=["navy"], avoid=[], outfits=[StylistOutfit(
        index=0, title="Quiet luxury autumn", style="Quiet luxury", style_definition="muted",
        slots=[StylistSlot(slot="blazer", role="accent", candidates=[
                   StylistCandidate(id="a", name="Wool blazer", colour="Green", price=40)]),
               StylistSlot(slot="trousers", role="neutral", candidates=[
                   StylistCandidate(id="b", name="Tailored trousers", colour="Navy", price=30)])])])

    class Broken:
        name = "broken"

        def curate_outfits(self, request):
            raise LLMTransientError("overloaded")

    assert stylist.curate(Broken(), runtime.redis, req, 0) == {}, "an error leaves the scorer's picks"

    calls = []

    class Counting(FakeVision):
        def curate_outfits(self, request):
            calls.append(request)
            return super().curate_outfits(request)

    first = stylist.curate(Counting(), runtime.redis, req, 0)
    assert first[0][0] == ["a", "b"] and "green" in first[0][1].lower() and first[0][2] is True
    assert stylist.curate(Counting(), runtime.redis, req, 0) == first and len(calls) == 1, "same page, no second call"
    assert stylist.curate(Counting(), runtime.redis, req.model_copy(update={"setting": "winter"}), 1)
    assert stylist.curate(Counting(), runtime.redis, req.model_copy(update={"setting": "spring"}), 1) == {}, \
        "past the daily cap the scorer's picks stand"
    assert len(calls) == 2


def test_a_personal_lookbook_says_why_each_outfit_works(client, runtime, user):
    upload(client, user["id"], SELFIE)
    run_next_job(runtime)
    lb = client.get(f"/api/users/{user['id']}/lookbook").json()
    assert lb["styled"] and all(o["why"] for s in lb["sections"] for o in s["outfits"])


def test_only_outfits_the_stylist_approves_are_shown(runtime):
    from lookmate.services.lookbook import build_lookbook
    from lookmate.services.ranking import UserContext

    class Rec:
        result = WINTER

    def strict(req):
        # Passes the first outfit, rejects the second, says nothing about the rest.
        first, second = req.outfits[0], req.outfits[1]
        return {0: ([s.candidates[0].id for s in first.slots], "Calm and tonal.", True),
                1: ([s.candidates[0].id for s in second.slots], "Green and blue fight.", False)}

    lb = build_lookbook(runtime.catalog, UserContext(), Rec, [], "seasons", season="autumn", stylist=strict)
    shown = lb["sections"][0]["outfits"]
    assert len(shown) == 1 and shown[0]["why"] == "Calm and tonal." and shown[0]["reviewed"]
    assert all("slots" not in o and "rejected" not in o for o in shown)

    # No stylist (no key, an error, the daily cap): the rule-checked outfits stand, marked unreviewed.
    plain = build_lookbook(runtime.catalog, UserContext(), Rec, [], "seasons", season="autumn", stylist=lambda r: {})
    assert len(plain["sections"][0]["outfits"]) == 3 and not plain["styled"]
    assert not any(o["reviewed"] for o in plain["sections"][0]["outfits"])


def test_vibes_name_a_style_and_a_season_or_occasion():
    from lookmate.services.lookbook import parse_vibe

    assert parse_vibe("Quiet luxury autumn") == ("quiet_luxury", "autumn", None)
    assert parse_vibe("date night coquette") == ("coquette", None, "date")
    assert parse_vibe("something in cashmere for fall") == ("quiet_luxury", "autumn", None)
    assert parse_vibe("hello") == (None, None, None)


def test_a_vibe_search_builds_that_style(client, runtime, user):
    lb = client.get(f"/api/users/{user['id']}/lookbook", params={"vibe": "quiet luxury winter"}).json()
    assert lb["vibe"] == "quiet luxury winter" and [s["id"] for s in lb["sections"]] == ["winter"]
    assert {o["style_id"] for o in lb["sections"][0]["outfits"]} == {"quiet_luxury"}
    party = client.get(f"/api/users/{user['id']}/lookbook", params={"vibe": "party y2k"}).json()
    assert [s["id"] for s in party["sections"]] == ["party"]


INSPIRATION = {"vibe": "Camel coat over a cream knit", "style_tags": ["old_money"], "sections": [
    {"item": {"category": "outerwear", "name": "wool coat", "colour": "camel", "fit": "long", "details": [],
              "style_tags": ["old_money"], "search_query": "camel long wool coat"}, "picks": []},
    {"item": {"category": "top", "name": "knit sweater", "colour": "white", "fit": "relaxed", "details": [],
              "style_tags": ["old_money"], "search_query": "white knit sweater"}, "picks": []},
]}


def test_make_it_mine_swaps_colours_that_dont_suit_and_says_why(runtime):
    from lookmate.services.make_it_mine import adapt_colour, make_it_mine
    from lookmate.services.ranking import UserContext

    pal = Palette.from_analysis(WINTER)
    new, why = adapt_colour("camel", pal, "cool winter")
    assert family(new) in {"grey", "navy", "black", "white"} and "avoid" in why and "cool winter" in why
    assert adapt_colour("white", pal)[0] == "white" and "already suits" in adapt_colour("white", pal)[1]
    assert family(adapt_colour("orange", pal)[0]) != "orange", "an accent stays an accent from the palette"

    mine = make_it_mine(INSPIRATION, runtime.catalog, UserContext(), WINTER)
    coat, knit = mine["pieces"]
    assert coat["original"]["colour"] == "camel" and coat["change"] and coat["colour"] != "camel"
    assert family(coat["pick"]["colour"]) != "beige", "the shop pick is in the new colour, not camel"
    assert knit["colour"] == "white"
    assert mine["total_price"] == pytest.approx(coat["pick"]["price"] + knit["pick"]["price"])
    assert not mine["reviewed"], "no stylist passed in"

    plain = make_it_mine(INSPIRATION, runtime.catalog, UserContext(), None)
    assert [p["colour"] for p in plain["pieces"]] == ["camel", "white"], "no colour analysis yet: nothing swapped"


def test_make_it_mine_goes_past_the_stylist_gate(runtime):
    from lookmate.services.make_it_mine import make_it_mine
    from lookmate.services.ranking import UserContext

    seen = []

    def reject(req):
        seen.append(req)
        return {0: ([s.candidates[0].id for s in req.outfits[0].slots], "The coat fights the knit.", False)}

    mine = make_it_mine(INSPIRATION, runtime.catalog, UserContext(), WINTER, stylist=reject)
    assert seen and mine["reviewed"] and mine["approved"] is False and mine["why"] == "The coat fights the knit."


def test_an_uploaded_look_can_be_made_mine(client, runtime, user):
    upload(client, user["id"], SELFIE)
    run_next_job(runtime)
    png = b"\x89PNG\r\n\x1a\n" + b"inspiration"
    look_id = client.post("/api/looks", data={"user_id": user["id"]},
                          files={"image": ("l.png", png, "image/png")}).json()["id"]
    assert client.get(f"/api/looks/{look_id}/mine").status_code == 409, "not analysed yet"
    run_next_job(runtime)
    mine = client.get(f"/api/looks/{look_id}/mine").json()
    assert mine["personal"] and mine["pieces"] and mine["reviewed"] and mine["approved"]
    assert all({"original", "colour", "change", "pick"} <= set(p) for p in mine["pieces"])


def test_by_occasion_builds_only_the_occasion_picked(client, user):
    """Renee: pick the occasion before Create my looks, and get just that occasion."""
    setup = client.get(f"/api/users/{user['id']}/lookbook/setup").json()
    assert [o["id"] for o in setup["occasions"]] == ["work", "weekend", "date", "party", "travel"]
    lb = client.get(f"/api/users/{user['id']}/lookbook", params={"mode": "occasions", "occasion": "date"}).json()
    assert [s["id"] for s in lb["sections"]] == ["date"] and lb["occasion"] == "date"
    every = client.get(f"/api/users/{user['id']}/lookbook", params={"mode": "occasions"}).json()
    assert len(every["sections"]) == 5, "without a pick, all occasions as before"
    app_js = client.get("/static/app.js").text
    assert "data-occasion" in app_js and "&occasion=${lbOccasion}" in app_js


def test_a_white_background_photo_wins_only_between_close_candidates(monkeypatch):
    """Feedback #34: packshots on white (Polyvore, Amazon) make cleaner flat lays than ASOS model shots,
    as a tie-breaker, never a filter."""
    from lookmate.catalog.service import ProductView, SearchResult
    from lookmate.services import lookbook
    from lookmate.services.ranking import UserContext

    def piece(pid):
        return ProductView(pid, "Black wool trousers", "trousers", "bottom", "black", "", "https://img/x.jpg", 25.0)

    def options(asos_sim, pv_sim):
        results = [SearchResult(piece("asos-1"), asos_sim), SearchResult(piece("pv-1"), pv_sim)]
        monkeypatch.setattr(lookbook, "search_in_range", lambda *a, **k: results)
        opts = lookbook._slot_options(None, UserContext(), Palette.from_analysis(None), "old_money", "bottom",
                                      "trousers", "black", set(), neutral=True)
        return [o["id"] for o in opts]

    assert options(0.80, 0.79) == ["pv-1", "asos-1"], "near-equal: the white-background photo first"
    assert options(0.80, 0.60) == ["asos-1", "pv-1"], "a clearly better match still wins"


def test_products_carry_their_shop_and_photo_background():
    from lookmate.catalog.service import ProductView

    def shop(pid):
        p = ProductView(pid, "Black trousers", "trousers", "bottom", "black", "", "", 25.0).to_dict()
        return p["shop"]["name"], p["shop"]["url"], p["white_background"]

    assert shop("asos-9")[0] == "ASOS" and shop("asos-9")[1].startswith("https://www.asos.com/") and not shop("asos-9")[2]
    assert shop("amz-B01") == ("Amazon", "https://www.amazon.com/dp/B01", True)
    assert shop("pv-3")[0] == "SHEIN" and shop("pv-3")[2], "Polyvore is gone: searched on SHEIN"


def test_the_why_line_is_one_short_sentence():
    from lookmate.services.stylist import short_why

    assert short_why("Soft camel and cream, easy and polished.") == "Soft camel and cream, easy and polished."
    assert short_why("Calm base. Then a second sentence nobody needs.") == "Calm base."
    long = short_why("One rust accent grounded by black and ivory and cream, true to the quiet luxury look with gold")
    assert len(long.split()) <= 12 and long.endswith(".") and not long.rstrip(".").split()[-1] in {"and", "with", "the"}
    assert short_why("") is None and short_why(None) is None


def test_the_offline_stylist_line_is_short(runtime):
    from lookmate.services.lookbook import build_lookbook
    from lookmate.services.ranking import UserContext
    from lookmate.services.stylist import curate

    class Rec:
        result = WINTER

    lb = build_lookbook(runtime.catalog, UserContext(), Rec, [], "seasons", season="autumn",
                        stylist=lambda req: curate(FakeVision(), runtime.redis, req, 0))
    whys = [o["why"] for s in lb["sections"] for o in s["outfits"] if o["why"]]
    assert whys and all(len(w.split()) <= 12 for w in whys), whys
