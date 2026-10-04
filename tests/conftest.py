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


@pytest.fixture
def client():
    from stylebuddy.main import app

    with TestClient(app) as c:
        yield c
