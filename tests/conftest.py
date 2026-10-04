import os

# Tests run fully offline: SQLite, the hash embedder and the bundled seed catalog.
os.environ.update({
    "DATABASE_URL": "sqlite://",
    "EMBEDDER": "hash",
    "CATALOG_SOURCE": "seed",
    "ANTHROPIC_API_KEY": "",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


import fakeredis  # noqa: E402
import redis  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kw: fakeredis.FakeRedis(server=server)))
    return fakeredis.FakeRedis(server=server)


@pytest.fixture
def client():
    from stylebuddy.main import app

    with TestClient(app) as c:
        yield c
    # Tests share one in-memory database; clear per-test data (the catalog is read-only and kept).
    from stylebuddy.db import Base, engine

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name != "products":
                conn.execute(table.delete())


@pytest.fixture
def runtime(client):
    return client.app.state.runtime


@pytest.fixture
def user(client):
    return client.post("/api/users", json={
        "nickname": "Renee", "height_cm": 165, "weight_kg": 55, "age": 26,
        "body_shape": "pear", "preferred_styles": ["old_money"], "budget_per_item": 30,
    }).json()
