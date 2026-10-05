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
