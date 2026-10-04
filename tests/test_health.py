def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
