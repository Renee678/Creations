def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_web_app_and_vocab_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "Lookmate" in page.text
    assert client.get("/static/app.js").status_code == 200
    vocab = client.get("/api/vocab").json()
    assert "old_money" in vocab["styles"] and "pear" in vocab["body_shapes"]


def test_product_photos_are_requested_without_a_referer(client):
    """Amazon's image CDN reset every hot-linked photo request that named the site as Referer."""
    assert '<meta name="referrer" content="no-referrer">' in client.get("/").text
