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
