"""Find dupes keeps the garment type, and prefers the same colour and length (Renee's ivory maxi skirt)."""

from lookmate.catalog.service import ProductView, SearchResult
from lookmate.llm.schemas import DetectedItem, LookAnalysis
from lookmate.services.dupes import find_dupes
from lookmate.services.match import Target, colour_match, length, subtype
from lookmate.services.price_range import PriceRange
from lookmate.services.ranking import UserContext, rank

SKIRT = DetectedItem(category="bottom", name="satin bias-cut maxi skirt", colour="ivory", fit="bias-cut, floor length",
                     details=["satin", "bias cut"], style_tags=["quiet_luxury"],
                     search_query="ivory satin bias cut maxi skirt")


def p(pid, name, colour, product_type="Skirts", price=30.0):
    return ProductView(pid, name, product_type, "bottom", colour, "", "", price)


# What the vector search returned on the server, closest first, plus the pieces it should have found.
STOCK = [
    (p("black-satin", "Satin midi skirt", "Black"), 0.95),
    (p("pink-mini", "Printed mini skirt", "Pink"), 0.93),
    (p("black-trousers", "Satin wide leg trousers", "Black", "Trousers"), 0.92),
    (p("dark-trousers", "Tailored trousers", "Navy", "Trousers"), 0.90),
    (p("ivory-maxi", "Satin maxi skirt", "Ivory"), 0.85),
    (p("cream-maxi", "Bias cut maxi skirt", "Cream"), 0.83),
    (p("white-midi", "Satin midi skirt", "White"), 0.80),
    (p("ivory-mini", "Satin mini skirt", "Ivory"), 0.79),
]


class FakeCatalog:
    def __init__(self, stock):
        self.stock = stock
        self.products = {prod.id: prod for prod, _ in stock}

    def search(self, query, k=5, category=None, max_price=None, exclude=None, min_price=None):
        return [SearchResult(prod, score) for prod, score in self.stock
                if (exclude is None or prod.id not in exclude) and (max_price is None or prod.price <= max_price)
                and (min_price is None or prod.price >= min_price)][:k]


def dupes(stock, item=SKIRT):
    analysis = LookAnalysis(is_outfit=True, vibe="", style_tags=[], items=[item])
    return find_dupes(analysis, FakeCatalog(stock), UserContext(), PriceRange(0, 60))["sections"][0]


def test_an_ivory_satin_maxi_skirt_gets_ivory_maxi_skirts():
    section = dupes(STOCK)
    ids = [x["id"] for x in section["picks"]]
    assert ids[:2] == ["ivory-maxi", "cream-maxi"], ids
    assert not {"black-satin", "pink-mini", "black-trousers", "dark-trousers", "ivory-mini"} & set(ids), \
        "no trousers, no mini, no black"
    assert "Same colour" in section["picks"][0]["reasons"] and "Same maxi length" in section["picks"][0]["reasons"]
    assert section["note"].startswith("Only 2 close matches"), "fewer picks, said plainly, rather than wrong ones"


def test_colour_and_length_never_relax_only_price_does():
    """Renee: accuracy before a full page. A white skirt search with no white skirt shows nothing, not a black one."""
    no_exact = [x for x in STOCK if x[0].id not in ("ivory-maxi", "cream-maxi")]
    section = dupes(no_exact)
    assert section["picks"] == [] and section["note"].startswith("No ivory maxi skirt in our catalog")

    pricey = [(p("ivory-maxi-dear", "Satin maxi skirt", "Ivory", price=90), 0.8)] + no_exact
    section = dupes(pricey)
    assert section["picks"] == [], "the price range is a hard filter too (Renee, 2026-10-06)"
    assert section["note"] == "Nothing in $0–$60 matches this piece. Widen the price range to see more.", \
        "the price is why, and it says so"


def test_garment_types_lengths_and_colours_are_read_from_words():
    assert subtype("bottom", "Denim skirt") == "skirt" and subtype("bottom", "Jean shorts") == "shorts"
    assert subtype("bottom", "Wide leg trousers") == "trousers" and subtype("bottom", "Straight jeans") == "jeans"
    assert subtype("top", "Oversized t-shirt") == "tee" and subtype("top", "Silk shirt") == "shirt"
    assert subtype("shoes", "Heeled ankle boots") == "boots" and subtype("shoes", "Ballet flats") == "flats"
    assert length("long-sleeve dress") is None and length("floor-length gown") == "maxi" and length("Mini skirt") == "mini"
    assert colour_match("cream", "white") == 0.5 and colour_match("cream", "black") == 0.0
    assert colour_match("navy", "black") == 0.5 and colour_match("pink", "pink") == 1.0
    t = Target("bottom", "maxi skirt", "ivory")
    assert t.subtype == "skirt" and t.length == "maxi" and t.colour == "cream"


def test_a_candidate_must_say_its_colour_and_length_to_count():
    target = Target("bottom", "satin maxi skirt", "white")
    assert target.check(p("a", "Satin maxi skirt", "Ivory")), "ivory and off-white read as white"
    assert not target.check(p("b", "Satin maxi skirt", "Beige")), "beige is not white"
    assert not target.check(p("c", "Satin maxi skirt", "")), "no colour given: skipped, not guessed"
    assert not target.check(p("d", "Satin skirt", "White")), "no length given: skipped, not guessed"
    assert not target.check(p("e", "Satin midi skirt", "White"))
    assert Target("bottom", "satin skirt", "").check(p("f", "Satin midi skirt", "Red")), "only stated rules apply"


def test_heather_grey_lounge_set_finds_grey_knits_and_trousers():
    """Renee's heather grey knit lounge set: trousers came back empty and the top got graphic sweatshirts."""
    from lookmate.services.colours import family

    assert {family(c) for c in ("heather grey", "grey marl", "charcoal", "light grey melange", "heather")} == {"grey"}
    assert family("heather blue") == "blue"

    trousers = Target("bottom", "wide-leg knit trousers", "heather grey", ["full length", "elasticated waist"])
    assert trousers.length is None, "maxi/midi/mini is a skirt and dress rule"
    assert trousers.check(p("t1", "Grey wide leg knit trouser", "Grey marl", "Trousers"))
    assert not trousers.check(p("t2", "Grey cropped knit trousers", "Grey", "Trousers")), "full length wants full length"
    assert Target("bottom", "cropped trousers", "grey").check(p("t3", "Capri trousers", "Grey", "Trousers"))

    top = Target("top", "knit long-sleeve top", "heather grey", ["plain", "ribbed"])

    def t(pid, name, colour="Grey", desc=""):
        return ProductView(pid, name, "Tops", "top", colour, desc, "", 30.0)

    assert top.check(t("k1", "Fine knit jumper", desc="Jumpers & Cardigans by Monki. Soft-touch knit"))
    assert not top.check(t("k2", "Disney Mickey Mouse knit jumper")), "a character print isn't a plain knit"
    assert not top.check(t("k3", "Knitted jumper with CREEPIN' IT REAL slogan")), "nor is a slogan"
    assert not top.check(t("k4", "Oversized jumper", desc="Hoodies & Sweatshirts by ASOS DESIGN. Printed front")), \
        "the shop files it under sweatshirts"
    assert not top.check(t("k5", "Sweatshirt in grey marl")), "a sweatshirt is not a knit"
    assert top.check(t("k6", "Knit jumper", desc="Jumpers & Cardigans by X. Spot clean only")), "'spot clean' is no print"
    floral = Target("top", "floral print knit jumper", "pink")
    assert floral.check(t("f1", "Floral knit jumper", "Pink")) and not floral.check(t("f2", "Plain knit jumper", "Pink"))


def test_nothing_outside_the_price_range_is_shown():
    """Renee: light blue wide-leg trousers, $0-$85, got $190 navy pencil jeans as "closest match"."""
    pricey = [(p("ivory-maxi-dear", "Satin maxi skirt", "Ivory", price=90), 0.8)]
    assert dupes(pricey)["picks"] == []


def test_a_piece_cut_off_at_the_edge_is_hidden_and_never_takes_a_main_pieces_pick():
    """Renee's cardigan photo: a sliver of trousers at the bottom edge became a trousers search."""
    cardigan = DetectedItem(category="top", name="button-front cardigan", colour="charcoal", fit="regular",
                            details=["knit"], style_tags=[], search_query="charcoal knit cardigan")
    sliver = DetectedItem(category="bottom", name="knit lounge trousers", colour="heather grey", fit="relaxed",
                          details=[], style_tags=[], search_query="grey knit trousers", partial=True)
    knits = FakeCatalog([(ProductView("c1", "Knit cardigan", "Cardigans", "top", "Charcoal", "", "", 25.0), 0.9),
                         (ProductView("t1", "Knit trousers", "Trousers", "bottom", "Grey", "", "", 25.0), 0.8)])
    result = find_dupes(LookAnalysis(is_outfit=True, vibe="", style_tags=[], items=[sliver, cardigan]),
                        knits, UserContext(), PriceRange(0, 100))
    first, second = result["sections"]
    assert first["item"]["name"] == "button-front cardigan" and not first["hidden"], "main pieces come first"
    assert second["hidden"] and second["item"]["partial"], "the sliver is hidden until the user ticks it"


def _product(pid, name, colour, category, product_type, price=40.0):
    return ProductView(pid, name, product_type, category, colour, "", "", price)


def test_light_blue_wide_leg_trousers_never_get_navy_pencil_jeans():
    """Renee (2026-10-06): light blue pinstripe wide-leg trousers got "denim pencil pants", dark navy, "Same colour"."""
    trousers = DetectedItem(category="bottom", name="high-waisted wide-leg trousers", colour="light blue",
                            fit="wide-leg", details=["high waist"], style_tags=["minimalist"],
                            search_query="light blue high-waisted wide-leg trousers")
    target = Target(trousers.category, trousers.name, trousers.colour, trousers.details, trousers.fit)
    jeans = _product("j", "Fleece lined pockets stretchy denim solid color pencil pants", "", "bottom", "Pants")
    assert subtype("bottom", jeans.name) == "jeans", "denim pants are jeans, not trousers"
    assert not target.check(jeans)
    assert not target.check(_product("s", "Light blue skinny trousers", "Light Blue", "bottom", "Trousers")), \
        "a wide-leg original never gets skinny"
    assert not target.check(_product("d", "Dark blue wide leg trousers", "Dark Blue", "bottom", "Trousers"))
    unshaded = _product("u", "Blue wide leg trousers", "Blue", "bottom", "Trousers")
    assert target.check(unshaded), "a plain 'blue' passes a light blue original (2026-10-06, jeans found nothing)"
    assert target.colour_score(unshaded) == 0.5, "but it isn't called the same colour"
    good = _product("ok", "Pale blue wide leg tailored trousers", "Pale Blue", "bottom", "Trousers")
    assert target.check(good)
    assert target.check(_product("st", "Light blue straight leg trousers", "Light Blue", "bottom", "Trousers")), \
        "straight sits with wide"

    analysis = LookAnalysis(is_outfit=True, vibe="", style_tags=[], items=[trousers])
    section = find_dupes(analysis, FakeCatalog([(jeans, 0.95), (good, 0.8)]), UserContext(), PriceRange(0, 85))["sections"][0]
    assert [x["id"] for x in section["picks"]] == ["ok"] and "Same colour" in section["picks"][0]["reasons"]


def test_jeans_and_trousers_never_stand_in_for_each_other():
    jeans = Target("bottom", "straight leg jeans", "blue")
    trousers = Target("bottom", "straight leg trousers", "black")
    assert not jeans.check(_product("t", "Straight leg trousers", "Blue", "bottom", "Trousers"))
    assert not trousers.check(_product("j", "Straight leg denim pants", "Black", "bottom", "Pants"))
    assert jeans.check(_product("j2", "Straight leg jeans", "Blue", "bottom", "Jeans"))


def test_a_cardigan_never_gets_a_jumper():
    """Renee (2026-10-06): a cream crew-neck cardigan got four jumpers filed under "Jumpers & Cardigans"."""
    target = Target("top", "fine-knit crew-neck cardigan", "cream", ["dark buttons"], "regular")
    jumpers = ["ASOS DESIGN cropped jumper in mini cable stitch", "ASOS DESIGN one shoulder jumper in cable",
               "ASOS DESIGN Petite crew neck jumper in sheer rib yarn", "JDY soft ribbed roll neck knitted jumper"]
    for name in jumpers:
        assert not target.check(_product(name, name, "Cream", "top", "Jumpers & Cardigans")), name
    assert target.check(_product("c", "ASOS DESIGN crew neck cardigan in fine knit", "Cream", "top", "Jumpers & Cardigans"))
    # The other way round, and a knit with buttons down the front is a cardigan even when not named one.
    jumper = Target("top", "cable knit jumper", "cream")
    assert not jumper.check(_product("c", "Fine knit cardigan", "Cream", "top", "Jumpers & Cardigans"))
    assert Target("top", "crew-neck knit", "cream", ["buttons down the front"]).subtype == "cardigan"


def test_same_colour_only_when_the_shade_agrees_too():
    target = Target("bottom", "wide leg jeans", "dark blue")
    assert target.colour == "navy"
    light = Target("top", "shirt", "light blue")
    assert light.colour_score(_product("a", "Light blue shirt", "Light Blue", "top", "Shirts")) == 1.0
    assert light.colour_score(_product("b", "Blue shirt", "Blue", "top", "Shirts")) == 0.5
    assert Target("top", "lightweight shirt", "blue").shade is None, "lightweight is not light"


def test_a_plain_short_sleeve_knit_never_gets_a_long_sleeve_slogan_sweatshirt():
    """Renee (2026-10-06): a heather grey short-sleeve boat-neck knit got Amazon's "Real knit grey", a long-sleeve
    sweatshirt with a big "CREEPIN' IT REAL" slogan, said only in its description."""
    from lookmate.services.match import sleeve

    target = Target("top", "short-sleeve boat-neck knit sweater", "heather grey", ["boat neck", "short sleeves"])
    assert target.sleeve == "short" and not target.patterned
    slogan = ProductView("a", "Real knit grey", "Sweaters", "top", "Grey",
                         "Long sleeve crew neck pullover with CREEPIN' IT REAL letter print on the front.", "", 80.0)
    assert not target.check(slogan)
    plain_long = ProductView("b", "Long sleeve knit jumper", "Jumpers", "top", "Grey", "", "", 30.0)
    assert not target.check(plain_long), "long sleeves for short"
    assert target.check(ProductView("c", "Short sleeve knit jumper", "Jumpers", "top", "Grey", "", "", 30.0))
    assert not target.check(ProductView("d", "Boat neck knit jumper", "Jumpers", "top", "Grey", "", "", 30.0)), \
        "a knit that doesn't say it has short sleeves has long ones (Renee, 2026-10-06)"
    assert target.check(ProductView("e", "Boat neck tee", "T-shirts", "top", "Grey", "", "", 30.0)) is False, \
        "still a knit original: a tee is another garment type"
    assert [sleeve(t) for t in ("Sleeveless top", "cap sleeve tee", "long-sleeved shirt", "crew neck")] == \
        ["sleeveless", "short", "long", None]


def test_a_summer_top_never_gets_a_winter_jumper():
    """Renee (2026-10-06): a red fitted mock-neck cap-sleeve top got a red high-neck jumper and a burgundy roll neck.
    Colour may be loose within its family; the season may not."""
    from lookmate.services.colours import family
    from lookmate.services.match import warmth

    top = DetectedItem(category="top", name="red mock-neck cap-sleeve fitted top", colour="red", fit="fitted",
                       details=["mock neck", "cap sleeves"], style_tags=[], search_query="red mock neck cap sleeve top",
                       sleeve="short", warmth="summer")
    target = Target.of(top)
    assert target.subtype is None and target.sleeve == "short" and target.warmth == "summer"
    jumpers = [ProductView("j1", "ASOS DESIGN longline jumper with high neck in red", "Jumpers & Cardigans", "top",
                           "Red", "", "", 30.0),
               ProductView("j2", "New Look roll neck knitted jumper in burgundy", "Jumpers & Cardigans", "top",
                           "Burgundy", "", "", 30.0)]
    for j in jumpers:
        assert not target.check(j), j.name
    tee = ProductView("t", "Fitted short sleeve tee in red", "T-shirts", "top", "Red", "", "", 15.0)
    assert target.check(tee)
    assert family("burgundy") == family("red"), "the colour family stays loose: dark red is still red"

    section = find_dupes(LookAnalysis(is_outfit=True, vibe="", style_tags=[], items=[top]),
                         FakeCatalog([(j, 0.95) for j in jumpers] + [(tee, 0.7)]), UserContext(), PriceRange(0, 50))
    assert [x["id"] for x in section["sections"][0]["picks"]] == ["t"]

    # And the other way round, from words alone when the model gives no warmth.
    coat = Target("outerwear", "padded puffer jacket", "black")
    assert coat.warmth == "winter"
    assert not coat.check(ProductView("l", "Linen jacket", "Jackets", "outerwear", "Black", "", "", 40.0))
    assert warmth("cable knit tank") is None, "says both: all-season, passes"


def test_light_blue_straight_jeans_take_mom_and_dad_jeans_in_light_wash():
    """Renee (2026-10-06): light blue high-rise straight-leg jeans got "No light blue jeans in our catalog yet":
    the leg rule wanted a stated straight leg and "lightwash" wasn't light. Only an opposite stated shape is out."""
    from lookmate.services.match import leg, shade

    target = Target("bottom", "high-rise straight-leg jeans", "light blue", ["high-rise", "straight leg"])
    assert target.subtype == "jeans" and target.leg == "wide" and target.shade == "light"
    jeans = lambda name, colour="Blue": _product(name, name, colour, "bottom", "Jeans")  # noqa: E731
    for name in ["ASOS DESIGN high rise 'original' mom jeans in lightwash blue", "Topshop Kort jeans in bleach",
                 "ASOS DESIGN dad jeans in light stone wash", "ASOS DESIGN straight leg jeans in light wash blue",
                 "ASOS DESIGN high rise jeans in light blue"]:
        assert target.check(jeans(name)), name
    assert not target.check(jeans("ASOS DESIGN skinny jeans in dark navy", "Navy")), "dark and skinny"
    assert not target.check(jeans("ASOS DESIGN skinny jeans in light blue")), "skinny is the opposite shape"
    assert not target.check(jeans("ASOS DESIGN straight leg jeans in darkwash blue")), "darkwash is dark"
    assert [leg("mom jeans"), leg("boyfriend jeans"), leg("jeggings"), leg("high rise jeans")] == \
        ["wide", "wide", "narrow", None]
    assert [shade("lightwash"), shade("acid wash"), shade("darkwash")] == ["light", "light", "dark"]


def test_jeans_that_just_say_blue_pass_a_light_blue_search():
    """Renee (2026-10-06): light blue straight jeans found "No light blue jeans in our catalog yet"; most catalog
    jeans say only "Blue". An unstated shade passes; a stated opposite one doesn't."""
    target = Target("bottom", "high-rise straight-leg jeans", "light blue", ["high-rise", "straight leg"])
    plain = _product("p", "ASOS DESIGN 90s straight jeans", "Blue", "bottom", "Jeans")
    assert target.check(plain)
    assert not target.check(_product("d", "ASOS DESIGN straight jeans in dark blue", "Dark Blue", "bottom", "Jeans"))
    reasons = rank(DetectedItem(category="bottom", name="high-rise straight-leg jeans", colour="light blue", fit="",
                                details=["straight leg"], style_tags=[], search_query=""),
                   [SearchResult(plain, 0.9)], UserContext())[0].reasons
    assert "Same colour" not in reasons


def test_light_blue_jeans_take_denim_named_by_its_wash_and_ignore_the_jeggings_label():
    """Renee (2026-10-07, after the redeploy): light blue straight jeans still found nothing. ASOS colours denim by
    its wash ("Bleach", "Lightwash", "Mid wash"), which had no colour family, and its "Jeans & Jeggings" label
    read every jean as narrow. "Slim straight" is a straight cut."""
    target = Target("bottom", "light blue high-rise straight-leg jeans", "light blue", ["high-rise", "straight leg"])
    for name, colour, label in [("Topshop Editor straight leg jeans", "Bleach", "Jeans"),
                                ("ASOS DESIGN mom jeans", "LIGHTWASH", "Jeans"),
                                ("Wrangler straight jeans", "Mid wash", "Jeans"),
                                ("Bershka high waist straight jeans in light blue", "Light Blue", "Jeans & Jeggings"),
                                ("Stradivarius slim straight jeans", "Blue", "Jeans")]:
        assert target.check(_product(name, name, colour, "bottom", label)), name
    assert not target.check(_product("dw", "ASOS DESIGN straight jeans", "Darkwash", "bottom", "Jeans")), "dark"
    assert not target.check(_product("bw", "ASOS DESIGN straight jeans", "Black wash", "bottom", "Jeans")), "black"
    assert not target.check(_product("sk", "ASOS DESIGN skinny jeans", "Lightwash", "bottom", "Jeans")), "skinny"
    assert not target.check(_product("jg", "ASOS DESIGN jeggings", "Lightwash", "bottom", "Jeans & Jeggings"))


def test_a_bangle_never_gets_earrings_or_a_necklace():
    """Renee (2026-10-06): a gold thin bangle got bow stud earrings, a pearl necklace and a beaded bracelet."""
    target = Target("accessory", "gold thin bangle bracelet", "gold")
    assert target.subtype == "bangle"
    acc = lambda name: _product(name, name, "Gold", "accessory", "Jewellery")  # noqa: E731
    for name in ["Bow stud earrings in gold", "Pearl necklace in gold", "Beaded bracelet in gold"]:
        assert not target.check(acc(name)), name
    assert target.check(acc("Thin bangle in gold"))
    assert subtype("accessory", "Studded leather belt") == "belt", "a studded belt isn't earrings"
    assert subtype("accessory", "Gold hoop earrings") == "earrings" and subtype("accessory", "Ear cuff") == "earrings"


def test_why_no_dupes_names_the_rule_that_rejects_each_close_product():
    """Renee (2026-10-07): light blue straight jeans still said "No light blue jeans in our catalog yet" after two
    fixes. The diagnostic lists the closest products and the first rule each one breaks, read-only."""
    from lookmate.why_no_dupes import explain

    item = DetectedItem(category="bottom", name="light blue high-rise straight-leg jeans", colour="light blue",
                        fit="straight", details=["high-rise"], style_tags=[], search_query="light blue jeans",
                        warmth="summer")
    stock = [(_product("t", "Wide leg trousers", "Light Blue", "bottom", "Trousers"), 0.9),
             (_product("s", "Skinny jeans in light wash", "Light Blue", "bottom", "Jeans"), 0.8),
             (_product("d", "Straight leg jeans in dark wash", "Dark Blue", "bottom", "Jeans"), 0.7),
             (_product("ok", "Straight leg jeans", "Light Blue", "bottom", "Jeans", price=60.0), 0.6)]
    target = Target.of(item)
    assert [target.rejection(p) for p, _ in stock] == ["type", "leg shape", "colour", None]
    lines = explain(FakeCatalog(stock), item, max_price=50)
    assert "4 products in bottom: 1 pass every rule, 0 of them at $50 or less" in lines[2]
    assert lines[3] == "Rejected by: type 1, leg shape 1, colour 1"
    assert lines[5].split()[0] == "type" and lines[-1].split()[0] == "PASS"


def test_why_no_dupes_finds_the_latest_look_with_that_piece(client, runtime, user):
    import pytest

    from lookmate.why_no_dupes import _item_from_look
    from tests.test_looks import run_next_job, upload

    upload(client, user["id"], data=b"\x89PNG\r\n\x1a\nwhy-no-dupes")
    assert run_next_job(runtime) == "done"
    item = _item_from_look(None, None)
    assert item.name and item.category
    with pytest.raises(SystemExit, match="no analysed look"):
        _item_from_look(None, "unicorn onesie")


def test_cropped_ankle_jeans_also_take_full_length_jeans():
    """Renee (2026-10-07): the AI read light blue straight jeans as "cropped ankle length"; the cropped rule then
    ruled out 106 of the catalog's 108 jeans, since shops rarely say a jean is cropped. Jeans only, one way only."""
    item = DetectedItem(category="bottom", name="light blue high-rise straight-leg jeans", colour="light blue",
                        fit="straight-leg, relaxed", details=["light wash denim", "high waist", "five-pocket styling",
                                                              "cropped ankle length", "button and zip fly"],
                        style_tags=[], search_query="Light blue wash high-rise straight-leg jeans with relaxed fit "
                        "and ankle-length cropped hem.", warmth="all-season")
    target = Target.of(item)
    assert target.cropped is True and target.subtype == "jeans"
    assert target.check(_product("k", "Topshop Curve Kort jeans in mid blue", "mid blue", "bottom", "Jeans"))
    assert target.check(_product("c", "Cropped straight jeans in light wash", "Light Blue", "bottom", "Jeans"))
    full = Target("bottom", "straight leg jeans", "light blue")
    assert full.rejection(_product("c2", "Cropped straight jeans in light wash", "Light Blue", "bottom", "Jeans")) \
        == "cropped", "a full-length jean still never gets a cropped one"
    trousers = Target("bottom", "cropped wide-leg trousers", "black")
    assert trousers.rejection(_product("t", "Wide leg trousers", "Black", "bottom", "Trousers")) == "cropped", \
        "trousers keep the rule: a culotte is not a full-length trouser"


def test_shops_named_skinny_fits_are_narrow():
    """Renee (2026-10-07): light blue straight jeans got "Topshop Curve Joni jeans in mid blue", a skinny fit that
    never says skinny. Shops' own names for their skinny fits count as narrow."""
    from lookmate.services.match import leg

    target = Target("bottom", "light blue high-rise straight-leg jeans", "light blue")
    for name in ["Topshop Curve Joni jeans in mid blue", "Topshop Jamie jeans in blue", "ASOS DESIGN Ridley high waist jeans",
                 "ASOS DESIGN Rivington jeggings in mid wash", "Levi's 721 high rise jeans in blue"]:
        assert leg(name) == "narrow", name
        assert target.rejection(_product(name, name, "mid blue", "bottom", "Jeans")) == "leg shape", name
    assert target.check(_product("k", "Topshop Curve Kort jeans in mid blue", "mid blue", "bottom", "Jeans"))
    assert leg("Jamie bag") == "narrow" and Target("bag", "Jamie shoulder bag", "black").leg is None, \
        "the leg rule only reads trousers and jeans"
