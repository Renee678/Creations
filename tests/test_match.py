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
    assert section["relaxed"] is None
    assert section["note"].startswith("Only 2 close matches"), "fewer picks, said plainly, rather than wrong ones"


def test_length_then_colour_relax_only_when_too_few_and_type_never_does():
    one_exact = [s for s in STOCK if s[0].id not in ("cream-maxi",)]
    section = dupes(one_exact)
    ids = [x["id"] for x in section["picks"]]
    assert section["relaxed"] == "length" and set(ids) == {"ivory-maxi", "white-midi"}
    assert any("Midi rather than maxi" in r for r in section["picks"][1]["reasons"])

    only_black = [s for s in STOCK if s[0].id in ("black-satin", "black-trousers", "dark-trousers")]
    section = dupes(only_black)
    assert [x["id"] for x in section["picks"]] == ["black-satin"] and section["relaxed"] == "colour"
    assert "Different colour" in section["picks"][0]["reasons"], "a relaxed pick says what differs"

    trousers_only = [s for s in STOCK if "trousers" in s[0].id]
    section = dupes(trousers_only)
    assert section["picks"] == [] and section["note"].startswith("No close match"), "never trousers for a skirt"


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
