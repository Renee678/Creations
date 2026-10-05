def test_search_finds_relevant_seed_products(client):
    res = client.get("/api/search", params={"q": "linen maxi dress beach", "k": 3}).json()["results"]
    assert res and res[0]["category"] == "dress"
    assert "linen" in res[0]["name"].lower()


def test_search_respects_filters(client):
    res = client.get("/api/search", params={"q": "knit", "category": "top", "max_price": 12}).json()["results"]
    assert res and all(r["category"] == "top" and r["price"] <= 12 for r in res)


def test_search_validates_input(client):
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "k": 500}).status_code == 422


def test_products_carry_shop_search_links(client):
    product = client.get("/api/search", params={"q": "puffer jacket", "k": 1}).json()["results"][0]
    links = product["shop_links"]
    assert links["shein"].startswith("https://us.shein.com/pdsearch/")
    assert links["asos"].startswith("https://www.asos.com/us/search/?q=")
    assert product["colour"].lower().split()[0] in links["asos"]


def test_search_text_does_not_repeat_the_colour():
    from lookmate.catalog.service import ProductView

    p = ProductView("1", "Black quilted jacket", "Jacket", "outerwear", "Black", "", "", 20.0)
    assert p.search_text == "black quilted jacket"
    assert ProductView("2", "Quilted jacket", "Jacket", "outerwear", "Beige", "", "", 20.0).search_text == "beige quilted jacket"
    assert p.shop_links()["shein"] == "https://us.shein.com/pdsearch/black%20quilted%20jacket/"


def test_saved_catalog_photos_are_served(client):
    from lookmate.main import CATALOG_IMAGES

    (CATALOG_IMAGES / "polyvore").mkdir(parents=True, exist_ok=True)
    (CATALOG_IMAGES / "polyvore" / "test_1.jpg").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    try:
        res = client.get("/catalog-images/polyvore/test_1.jpg")
        assert res.status_code == 200 and res.content.startswith(b"\xff\xd8")
    finally:
        (CATALOG_IMAGES / "polyvore" / "test_1.jpg").unlink()


def test_app_is_installable_on_phones(client):
    import json

    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200
    m = json.loads(manifest.content)
    assert m["display"] == "standalone" and m["start_url"] == "/"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes and any(i["purpose"] == "maskable" for i in m["icons"])
    for icon in m["icons"]:
        assert client.get(icon["src"]).content.startswith(b"\x89PNG")

    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert "/api" not in sw.text.split("const SHELL")[1].split(";")[0], "API responses are never cached"
    page = client.get("/").text
    assert 'rel="manifest"' in page and 'rel="apple-touch-icon"' in page


def test_tryon_asks_for_a_photo_instead_of_popping_the_picker(client):
    # A new phone has no saved try-on photo: explain why and let the user tap to choose one,
    # rather than opening the file picker by surprise.
    app_js = client.get("/static/app.js").text
    start = app_js.split("function startTryOn")[1].split("\n}\n")[0]
    assert "data-tryon-pick" in start and '#tryon-file").click()' not in start
    assert '[data-tryon-pick]")) return $("#tryon-file").click()' in app_js


def test_lookbook_has_a_fitting_room_for_mixing_outfits(client, runtime, user):
    page = client.get("/").text
    assert 'id="room"' in page and 'id="tryon-room"' in page
    app_js = client.get("/static/app.js").text
    assert "data-room=" in app_js and 'startTryOn("room")' in app_js
    # Mixed pieces are sent like any outfit: the API takes any pieces from the catalog.
    by_cat = {}
    for p in runtime.catalog.products.values():
        by_cat.setdefault(p.category, p.id)
    ids = [by_cat["top"], by_cat["bottom"], by_cat["shoes"]]
    res = client.post(f"/api/users/{user['id']}/tryons", data={"product_ids": ",".join(ids)},
                      files={"photo": ("me.jpg", b"\xff\xd8\xff\xe0me", "image/jpeg")})
    assert res.status_code == 202 and res.json()["product_ids"] == ids


def test_lookbook_asks_for_photos_before_showing_outfits(client):
    app_js = client.get("/static/app.js").text
    gate = app_js.split("async function loadLookbook")[1]
    assert "!lb.personal" in gate and "lb-gate" in gate and "data-lb-browse" in gate
    # The fitting room tray can add the try-on photo itself, so Try it on me always works.
    assert "data-room-photo" in app_js and 'pendingOutfit = "room"' in app_js


def test_my_style_is_a_report_and_trends_filter_by_season_and_style(client):
    page = client.get("/").text
    assert 'id="style-report"' in page and 'id="trend-seasons"' in page and 'id="trend-styles"' in page
    app_js = client.get("/static/app.js").text
    assert "Your colour season" in app_js and "Your style DNA" in app_js
    assert "See your full report" in app_js, "the Lookbook links to the report instead of repeating it"
    assert "/api/trends${userId ? `?user_id=${userId}`" in app_js, "trends are fetched with the user, for fit verdicts"


def test_app_files_are_revalidated_so_a_deploy_shows_up(client):
    for path in ("/", "/static/app.js", "/static/style.css"):
        res = client.get(path)
        assert res.headers["cache-control"] == "no-cache", path
    again = client.get("/static/app.js", headers={"If-None-Match": client.get("/static/app.js").headers["etag"]})
    assert again.status_code == 304, "unchanged files are still cheap to check"


def test_try_on_lives_in_the_fitting_room_only(client):
    app_js = client.get("/static/app.js").text
    card = app_js.split("function outfitCard")[1].split("\n}\n")[0]
    assert "tryon-btn" not in card and "data-room-all" in card, "outfits fill the fitting room instead of starting try-ons"
    assert "if (tryonRunning) return;" in app_js, "one try-on at a time"
    assert "/photo-check" in app_js, "a picked photo is checked before use"
    assert 'room = {}; store.set("room", "{}")' in app_js, "new photos start an empty fitting room"
    assert "tryon-clock" in app_js and "TRYON_GIVE_UP_MS" in app_js, "elapsed time and a hard stop instead of an endless spinner"
