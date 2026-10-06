"""Find dupes keeps the garment type, and prefers the same colour and length (Renee's ivory maxi skirt)."""

from lookmate.catalog.service import ProductView, SearchResult
from lookmate.llm.schemas import DetectedItem, LookAnalysis
from lookmate.services.dupes import find_dupes
from lookmate.services.match import Target, colour_match, length, subtype
from lookmate.services.price_range import PriceRange
from lookmate.services.ranking import UserContext

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
