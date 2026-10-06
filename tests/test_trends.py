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


ITEM = TrendItem(season="autumn", kind="pieces", style_id="old_money", label="Old money", description="Understated", keywords=["a"],
                 example_query="beige cardigan", sources=["https://example.com"])


def test_offline_refresh_uses_seed_and_api_serves_examples(client):
    with SessionLocal() as s:
        trends.refresh(s, None, DATA)
    body = client.get("/api/trends").json()
    assert body["origin"] == "seed" and len(body["trends"]) >= 5
    assert body["seasons"] == ["spring", "summer", "autumn", "winter"]
    assert body["current_season"] in body["seasons"]
    for season in body["seasons"]:
        kinds = {t["kind"] for t in body["trends"] if t["season"] == season}
        assert kinds == set(body["kinds"]), f"{season} has every kind: pieces, bags and shoes, beauty, colours"
    for t in body["trends"]:
        assert t["colours"] and t["fit"] is None, "no fit verdict without a user"
        assert bool(t["examples"]) == (t["kind"] != "beauty"), "shoppable trends link to products; makeup doesn't"


def test_researched_trends_replace_seed(client):
    with SessionLocal() as s:
        trends.refresh(s, None, DATA)
        trends.refresh(s, FakeResearcher([ITEM]), DATA)
    body = client.get("/api/trends").json()
    assert body["origin"] == "web" and body["trends"][0]["label"] == "Old money"
    researched = body["trends"][0]["season"]
    assert [t["label"] for t in body["trends"] if t["season"] == researched] == ["Old money"], "seed gone for that season"
    assert {t["season"] for t in body["trends"]} == set(trends.SEASONS), "seasons not researched yet come from the seed"


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


def test_outdated_trends_table_is_rebuilt(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from lookmate.db import Base
    from lookmate.runtime import _drop_outdated_trends

    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE trends (id INTEGER PRIMARY KEY, label_zh TEXT, description_zh TEXT)"))
    _drop_outdated_trends(eng)
    Base.metadata.create_all(eng)
    assert {"label", "season", "kind"} <= {c["name"] for c in inspect(eng).get_columns("trends")}


def test_a_trend_says_whether_it_suits_you():
    from types import SimpleNamespace

    from lookmate.services.lookbook import Palette
    from lookmate.services.trends import trend_fit

    autumn = Palette.from_analysis({"best_colours": [{"name": "camel"}, {"name": "olive"}, {"name": "rust"}],
                                    "avoid_colours": [{"name": "icy pink"}, {"name": "fuchsia"}]})
    trend = lambda *colours, style="old_money": SimpleNamespace(colours=[{"name": c} for c in colours], style_id=style)
    assert trend_fit(trend("khaki", "camel"), autumn, ["old_money"]) == {
        "verdict": "suits", "notes": ["Khaki is in your colours", "Matches your style"]}
    adapt = trend_fit(trend("blush pink"), autumn, [])
    assert adapt["verdict"] == "adapt" and "try it in camel" in adapt["notes"][0]
    assert trend_fit(trend("cobalt blue"), autumn, [])["verdict"] == "neutral"
    assert trend_fit(trend("camel"), Palette.from_analysis(None), []) is None, "no analysis, no verdict"


def test_trends_for_a_user_carry_a_fit_verdict(client, runtime, user):
    with SessionLocal() as s:
        trends.refresh(s, None, DATA)
    body = client.get("/api/trends", params={"user_id": user["id"]}).json()
    assert body["my_styles"][0]["id"] == "old_money"
    # No photo analysis yet: no colour verdict to give.
    assert all(t["fit"] is None for t in body["trends"])


def test_the_trends_page_is_never_empty(client):
    body = client.get("/api/trends").json()
    assert body["origin"] == "seed" and body["trends"], "nothing stored yet: the seed trends are served"


def test_seed_trends_show_while_research_runs_and_a_failure_backs_off(client, fake_redis):
    broken = FakeResearcher(fail=True)
    with SessionLocal() as s:
        assert trends.refresh_if_due(s, fake_redis, broken, DATA) is False
        assert trends.latest_batch(s)[0].origin == "seed", "seed stored first, so the page has content"
        assert trends.refresh_if_due(s, fake_redis, broken, DATA) is False
        assert broken.calls == 1, "a failed research isn't retried every 10 minutes"
        fake_redis.delete(trends.RETRY_KEY)
        assert trends.refresh_if_due(s, fake_redis, FakeResearcher([ITEM]), DATA) is True
        assert trends.latest_batch(s)[0].origin == "web", "a seed batch is replaced once research works"


def test_research_searches_first_then_structures_without_tools():
    """Web search answers carry citations, which structured output doesn't accept: two calls, not one."""
    from types import SimpleNamespace as NS

    calls = []

    class Messages:
        def create(self, **kw):
            calls.append(("create", kw))
            return NS(stop_reason="end_turn", usage={}, content=[
                NS(type="server_tool_use"), NS(type="text", text="Burgundy everything, see https://example.com")])

        def parse(self, **kw):
            calls.append(("parse", kw))
            return NS(stop_reason="end_turn", usage={}, parsed_output=trends.TrendReport(trends=[ITEM]))

    r = trends.ClaudeTrendResearcher.__new__(trends.ClaudeTrendResearcher)
    r._client, r._model = NS(messages=Messages()), "m"
    assert r.research() == [ITEM]
    (first, search), (second, structure) = calls
    assert first == "create" and search["tools"][0]["name"] == "web_search" and "output_format" not in search
    assert second == "parse" and "tools" not in structure and "https://example.com" in structure["messages"][0]["content"]


def test_every_season_has_trends_when_research_covered_two(client, runtime):
    """Trends offers all four seasons; research covers two, the seed fills the others instead of an empty page."""
    from lookmate.db import SessionLocal
    from lookmate.services.trends import load_seed, store_batch

    researched = [t for t in load_seed(runtime.data_dir) if t.season in ("autumn", "winter")]
    with SessionLocal() as s:
        store_batch(s, researched, "claude")
    body = client.get("/api/trends").json()
    assert body["seasons"] == ["spring", "summer", "autumn", "winter"] and body["year"] >= 2026
    by_season = {}
    for t in body["trends"]:
        by_season.setdefault(t["season"], set()).add(t.get("label"))
    assert all(by_season.get(s) for s in body["seasons"]), "no season is empty"
