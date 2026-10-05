"""Saved outfits: whole outfits kept from the lookbook, Make it mine or the fitting room ("My outfits")."""


def pieces(runtime, *categories):
    by_cat = {}
    for p in runtime.catalog.products.values():
        by_cat.setdefault(p.category, p.id)
    return [by_cat[c] for c in categories]


def test_save_list_filter_and_delete_an_outfit(client, runtime, user):
    url = f"/api/users/{user['id']}/outfits"
    ids = pieces(runtime, "top", "bottom", "shoes")
    res = client.post(url, json={"title": "Sunday brunch", "product_ids": ids, "season": "autumn",
                                 "style_id": "old_money"})
    assert res.status_code == 201
    saved = res.json()
    assert saved["title"] == "Sunday brunch" and [p["id"] for p in saved["pieces"]] == ids
    assert saved["total_price"] > 0 and saved["style"] == "Old money"

    again = client.post(url, json={"product_ids": list(reversed(ids))})
    assert again.status_code == 200 and again.json()["id"] == saved["id"], "the same set saved twice is one outfit"

    client.post(url, json={"product_ids": pieces(runtime, "dress", "shoes"), "season": "summer", "style_id": "boho"})
    everything = client.get(url).json()
    assert everything["total"] == 2 and everything["seasons"] == ["summer", "autumn"]
    assert {s["id"] for s in everything["styles"]} == {"old_money", "boho"}
    assert [o["title"] for o in client.get(url, params={"season": "autumn"}).json()["outfits"]] == ["Sunday brunch"]
    assert client.get(url, params={"style": "boho"}).json()["outfits"][0]["season"] == "summer"

    assert client.delete(f"{url}/{saved['id']}").status_code == 204
    assert client.get(url).json()["total"] == 1
    assert client.delete(f"{url}/{saved['id']}").status_code == 404


def test_a_saved_outfit_gets_a_season_style_and_title_when_none_is_given(client, runtime, user):
    out = client.post(f"/api/users/{user['id']}/outfits",
                      json={"product_ids": pieces(runtime, "top", "bottom"), "source": "fitting_room"}).json()
    assert out["season"] in {"spring", "summer", "autumn", "winter"} and out["style_id"]
    assert out["title"] and out["source"] == "fitting_room"


def test_saving_an_outfit_teaches_the_style_memory(client, runtime, user):
    before = client.get(f"/api/users/{user['id']}/style").json()["signals"]
    client.post(f"/api/users/{user['id']}/outfits", json={"product_ids": pieces(runtime, "top", "bottom", "bag")})
    assert client.get(f"/api/users/{user['id']}/style").json()["signals"] > before


def test_bad_outfits_are_rejected(client, runtime, user):
    url = f"/api/users/{user['id']}/outfits"
    assert client.post(url, json={"product_ids": pieces(runtime, "top")}).status_code == 422, "one piece isn't an outfit"
    assert client.post(url, json={"product_ids": ["nope", "nada"]}).status_code == 404
    other = client.post("/api/users", json={"nickname": "B", "height_cm": 160, "weight_kg": 50, "age": 30,
                                            "body_shape": "pear", "preferred_styles": [], "budget_per_item": 30}).json()
    mine = client.post(url, json={"product_ids": pieces(runtime, "top", "bottom")}).json()
    assert client.delete(f"/api/users/{other['id']}/outfits/{mine['id']}").status_code == 404, "only the owner deletes"


def test_a_look_book_page_keeps_the_stylist_line_occasion_and_can_be_renamed(client, runtime, user):
    url = f"/api/users/{user['id']}/outfits"
    ids = pieces(runtime, "top", "bottom", "shoes")
    saved = client.post(url, json={"product_ids": ids, "why": "Soft neutrals, sharp tailoring.", "occasion": "work"}).json()
    assert saved["why"] == "Soft neutrals, sharp tailoring." and saved["occasion"] == "work"
    assert saved["tryon_image"] is None and saved["inspo"] is None, "flat lay until a try-on exists"
    listed = client.get(url).json()
    assert listed["this_season"] == 1 and listed["outfits"][0]["why"].startswith("Soft")

    renamed = client.patch(f"{url}/{saved['id']}", json={"title": "Monday office"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Monday office"
    assert client.get(url).json()["outfits"][0]["title"] == "Monday office"
    assert client.patch(f"{url}/999", json={"title": "x"}).status_code == 404
    assert client.patch(f"{url}/{saved['id']}", json={"title": ""}).status_code == 422


def test_the_on_me_view_uses_the_latest_try_on_of_the_same_pieces(client, runtime, user):
    from lookmate.db import SessionLocal
    from lookmate.models import TryOn

    url = f"/api/users/{user['id']}/outfits"
    ids = pieces(runtime, "top", "bottom")
    client.post(url, json={"product_ids": ids})
    with SessionLocal() as s:
        s.add(TryOn(id="t-other", user_id=user["id"], request_sha256="a", product_ids=[ids[0]], media_type="image/jpeg",
                    status="done", result_image=b"x"))
        s.add(TryOn(id="t-match", user_id=user["id"], request_sha256="b", product_ids=list(reversed(ids)),
                    media_type="image/jpeg", status="done", result_image=b"jpg"))
        s.commit()
    assert client.get(url).json()["outfits"][0]["tryon_image"] == "/api/tryons/t-match/image"


def test_palette_dots_show_other_colours_of_a_piece_that_suit_the_user(client, runtime, user, monkeypatch):
    from lookmate.api import style
    from lookmate.catalog.service import ProductView

    skirt = ProductView("s-black", "Satin midi skirt in black", "Skirts", "bottom", "black", "", "", 20.0)
    variants = [ProductView("s-red", "Satin midi skirt in red", "Skirts", "bottom", "red", "", "", 20.0),
                ProductView("s-navy", "Satin midi skirt in navy", "Skirts", "bottom", "navy", "", "", 20.0),
                ProductView("s-orange", "Satin midi skirt in orange", "Skirts", "bottom", "orange", "", "", 20.0),
                ProductView("t-red", "Satin top in red", "Tops", "top", "red", "", "", 20.0)]
    for p in [skirt, *variants]:
        monkeypatch.setitem(runtime.catalog.products, p.id, p)
    monkeypatch.setattr(runtime.catalog, "_variants", None)
    palette = {"red": "#c4142f", "navy": "#1f2a48", "black": "#141414"}
    dots = style.palette_dots(skirt, runtime.catalog, palette)
    assert {d["id"] for d in dots} == {"s-red", "s-navy"}, "same skirt, other colours, only those in the palette"
    assert dots[0]["hex"] in palette.values()
    assert style.palette_dots(skirt, runtime.catalog, {"green": "#0b7a5a"}) == [], "hidden when nothing matches"


def test_new_outfit_columns_are_added_to_an_existing_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from lookmate.runtime import _add_new_columns

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:  # the table as the server had it before the Look Book
        conn.execute(text("CREATE TABLE saved_outfits (id INTEGER PRIMARY KEY, user_id INTEGER, title VARCHAR(80),"
                          " product_ids JSON, pieces_key VARCHAR(64), season VARCHAR(10), style_id VARCHAR(30),"
                          " source VARCHAR(20), created_at DATETIME)"))
    _add_new_columns(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("saved_outfits")}
    assert {"why", "occasion", "inspo_look_id"} <= cols
    _add_new_columns(eng)  # idempotent
