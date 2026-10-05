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
