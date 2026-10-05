from lookmate.catalog.service import ProductView, SearchResult
from lookmate.llm.schemas import DetectedItem
from lookmate.services.ranking import UserContext, price_score, rank

ITEM = DetectedItem(category="bottom", name="wide-leg trousers", colour="beige", fit="wide-leg", details=[],
                    style_tags=["old_money"], search_query="beige wide-leg trousers")


def product(pid, name, colour="Beige", price=15.0, desc=""):
    return ProductView(pid, name, "Trousers", "bottom", colour, desc, "", price)


def test_similarity_dominates_but_budget_breaks_ties():
    cands = [SearchResult(product("a", "Trousers A", price=55), 0.80), SearchResult(product("b", "Trousers B", price=9), 0.79)]
    assert rank(ITEM, cands, UserContext(budget_per_item=30))[0].product_id == "b"
    far = [SearchResult(product("a", "Trousers A", price=55), 0.95), SearchResult(product("b", "Trousers B", price=9), 0.40)]
    assert rank(ITEM, far, UserContext(budget_per_item=30))[0].product_id == "a"


def test_body_shape_fit_reorders_close_candidates():
    cands = [SearchResult(product("skinny", "Skinny low-rise jeans"), 0.70),
             SearchResult(product("wide", "Wide high-waisted trousers"), 0.69)]
    assert rank(ITEM, cands, UserContext(body_shape="pear"))[0].product_id == "wide"


def test_reasons_explain_the_pick():
    cands = [SearchResult(product("a", "Tailored trousers", desc="old money, tailored"), 0.7)]
    reasons = rank(ITEM, cands, UserContext(style_weights={"old_money": 0.3}))[0].reasons
    assert "Same colour" in reasons and "Matches your old money style" in reasons and "Within your budget" in reasons


def test_colour_variants_of_one_design_do_not_crowd_out_other_designs():
    cands = [SearchResult(product(f"v{i}", "Same trousers", colour=c), 0.9) for i, c in enumerate(["Beige", "Black", "White"])]
    cands.append(SearchResult(product("other", "Different trousers"), 0.5))
    ids = [p.product_id for p in rank(ITEM, cands, UserContext(), k=2)]
    assert "other" in ids


def test_price_score_bounds():
    assert price_score(0, 30) == 1 and price_score(30, 30) == 0.5 and price_score(90, 30) == 0


def test_style_name_keeps_acronyms():
    from lookmate.services.vocab import style_name

    assert style_name("old_money") == "old money" and style_name("y2k") == "Y2K"


def test_a_sporty_piece_is_never_a_dupe_for_a_tailored_one():
    from lookmate.services.ranking import register

    assert register("Wide-leg tailored trousers in navy") == "tailored"
    assert register("Jersey joggers with drawstring") == "sporty"
    assert register("Plain cotton t-shirt") is None
    assert register("Pumpkin knit jumper") is None, "words match whole, not inside other words"

    item = DetectedItem(category="bottom", name="tailored trousers", colour="navy", fit="straight",
                        details=["pressed crease"], style_tags=["office"], search_query="navy tailored trousers")
    jogger = ProductView("j", "Navy track joggers", "Trousers", "bottom", "Navy", "", "", 20)
    trouser = ProductView("t", "Navy pleated trousers", "Trousers", "bottom", "Navy", "", "", 25)
    plain = ProductView("p", "Navy straight trousers", "Trousers", "bottom", "Navy", "", "", 22)
    picks = rank(item, [SearchResult(jogger, 0.95), SearchResult(trouser, 0.8), SearchResult(plain, 0.7)], UserContext())
    assert [p.product_id for p in picks] == ["t", "p"], "the closer-looking joggers are left out"
