from datetime import datetime, timedelta, timezone
from pathlib import Path

from lookmate.db import SessionLocal
from lookmate.services import trends
from lookmate.services.trends import TrendItem

DATA = Path(__file__).resolve().parents[1] / "data"


class FakeResearcher:
    name = "web"

    def __init__(self, items=None, fail=False):
        self.items, self.fail, self.calls = items or [], fail, 0

    def research(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("search down")
        return self.items


ITEM = TrendItem(style_id="old_money", label_zh="老钱风", description_zh="低调", keywords=["a"],
                 example_query="beige cardigan", sources=["https://example.com"])


def test_offline_refresh_uses_seed_and_api_serves_examples(client):
    with SessionLocal() as s:
        trends.refresh(s, None, DATA)
    body = client.get("/api/trends").json()
    assert body["origin"] == "seed" and len(body["trends"]) >= 5
    assert all(t["examples"] for t in body["trends"]), "every trend links to catalog products"


def test_researched_trends_replace_seed(client):
    with SessionLocal() as s:
        trends.refresh(s, None, DATA)
        trends.refresh(s, FakeResearcher([ITEM]), DATA)
    body = client.get("/api/trends").json()
    assert body["origin"] == "web" and [t["label_zh"] for t in body["trends"]] == ["老钱风"]


def test_failed_research_keeps_last_good_batch(client):
    with SessionLocal() as s:
        good = trends.refresh(s, FakeResearcher([ITEM]), DATA)
        assert trends.refresh(s, FakeResearcher(fail=True), DATA) == good


def test_refresh_only_when_stale_and_under_lock(client, fake_redis):
    r = FakeResearcher([ITEM])
    with SessionLocal() as s:
        assert trends.refresh_if_due(s, fake_redis, r, DATA) is True
        assert trends.refresh_if_due(s, fake_redis, r, DATA) is False  # fresh now
        assert not trends.is_stale(s)
        assert trends.is_stale(s, now=datetime.now(timezone.utc) + timedelta(days=8))
    assert r.calls == 1


def test_lock_prevents_concurrent_refresh(client, fake_redis):
    fake_redis.set(trends.LOCK_KEY, "1")
    with SessionLocal() as s:
        assert trends.refresh_if_due(s, fake_redis, FakeResearcher([ITEM]), DATA) is False
