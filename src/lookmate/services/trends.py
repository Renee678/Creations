"""Seasonal trend radar.

Xiaohongshu, TikTok and Instagram have no public API and forbid scraping, so the
researcher asks Claude to use web search over public fashion coverage and
return a structured list of trends. A bundled seed list keeps the page useful
offline and whenever research fails.

Refresh is triggered by the worker on a schedule; a Redis lock makes sure only
one process refreshes at a time.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Trend
from .vocab import STYLES

log = logging.getLogger(__name__)

REFRESH_EVERY = timedelta(days=7)
LOCK_KEY = "lock:trends-refresh"


SEASONS = ("spring", "summer", "autumn", "winter")
KINDS = {
    "pieces": "Key pieces",
    "bags_shoes": "Bags and shoes",
    "beauty": "Makeup and hair",
    "colour": "Colours",
}


def season_of(month: int) -> str:
    """Northern-hemisphere fashion season for a month (1-12)."""
    return SEASONS[((month - 3) % 12) // 3]


class TrendColour(BaseModel):
    name: str = Field(description="Plain English colour name, e.g. 'berry', 'butter yellow'")
    hex: str = Field(description="Hex code like #7d2448")


class TrendItem(BaseModel):
    season: Literal["spring", "summer", "autumn", "winter"] = Field(description="Season this trend is for")
    kind: Literal["pieces", "bags_shoes", "beauty", "colour"] = Field(
        description="pieces = clothing; bags_shoes = bags, shoes, jewellery; beauty = makeup, nails, hair; colour = a trend colour")
    style_id: str = Field(description=f"Closest label from: {', '.join(STYLES)}")
    label: str = Field(description="Short English trend name as people search for it, e.g. 'Old money'")
    description: str = Field(description="Two English sentences: what it looks like and its key pieces")
    keywords: list[str] = Field(description="5 popular search keywords in English")
    example_query: str = Field(
        description="English product-search sentence listing 2-4 signature pieces; empty for beauty trends")
    colours: list[TrendColour] = Field(default_factory=list, description="1-3 colours that define the trend")
    sources: list[str] = Field(default_factory=list, description="URLs of the articles this is based on")


class TrendReport(BaseModel):
    trends: list[TrendItem] = Field(description="12 to 20 distinct trends across the two seasons and four kinds")


RESEARCH_PROMPT = """Research women's fashion and beauty trends for {season} and {next_season} {year} on
Xiaohongshu (RED), TikTok, Instagram and Pinterest, plus trend reports such as Pinterest Predicts,
the Lyst Index and Vogue. Use web search over public articles; prefer sources from the last three
months. For each of the two seasons, return trends of all four kinds: key clothing pieces, bags and
shoes, makeup and hair, and colours. Name each one the way people search for it, map it to the
closest style label, give the 1-3 colours that define it, and keep only trends you saw evidence for.
Cite the URLs you relied on in each trend's sources."""


def load_seed(data_dir: Path) -> list[TrendItem]:
    return [TrendItem(**t) for t in json.loads((data_dir / "seed_trends.json").read_text())]


class ClaudeTrendResearcher:
    name = "web"

    def __init__(self, api_key: str, model: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, timeout=300, max_retries=2)
        self._model = model

    def research(self) -> list[TrendItem]:
        now = datetime.now(timezone.utc)
        season = season_of(now.month)
        next_season = SEASONS[(SEASONS.index(season) + 1) % 4]
        year = now.year
        messages = [{"role": "user", "content": RESEARCH_PROMPT.format(season=season, next_season=next_season, year=year)}]
        for _ in range(3):  # server tools may pause a long turn; resume it
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=16000,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
                messages=messages,
                output_format=TrendReport,
            )
            if response.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": response.content}]
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"trend research produced no report (stop_reason={response.stop_reason})")
        return response.parsed_output.trends


def store_batch(session: Session, items: list[TrendItem], origin: str) -> str:
    batch = uuid.uuid4().hex
    for t in items:
        session.add(Trend(
            batch_id=batch, style_id=t.style_id if t.style_id in STYLES else "minimalist",
            label=t.label, description=t.description, keywords=t.keywords[:8],
            example_query=t.example_query, sources=t.sources[:5], origin=origin,
            season=t.season, kind=t.kind, colours=[c.model_dump() for c in t.colours[:3]],
        ))
    session.commit()
    return batch


def latest_batch(session: Session) -> list[Trend]:
    newest = session.scalar(select(Trend).order_by(Trend.refreshed_at.desc(), Trend.id.desc()).limit(1))
    if newest is None:
        return []
    return list(session.scalars(select(Trend).where(Trend.batch_id == newest.batch_id).order_by(Trend.id)))


def is_stale(session: Session, now: datetime | None = None) -> bool:
    batch = latest_batch(session)
    if not batch:
        return True
    now = now or datetime.now(timezone.utc)
    refreshed = batch[0].refreshed_at
    if refreshed.tzinfo is None:  # SQLite drops tz info
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    return now - refreshed > REFRESH_EVERY


def refresh(session: Session, researcher, data_dir: Path) -> str:
    """Research new trends; fall back to the seed list so the page is never empty."""
    if researcher is not None:
        try:
            items = researcher.research()
            if items:
                log.info("trend research returned %d trends", len(items))
                return store_batch(session, items, researcher.name)
        except Exception:
            log.exception("trend research failed; using seed trends")
    if latest_batch(session):
        return latest_batch(session)[0].batch_id  # keep the last good batch rather than overwrite with seed
    return store_batch(session, load_seed(data_dir), "seed")


def refresh_if_due(session: Session, redis_client, researcher, data_dir: Path) -> bool:
    if not is_stale(session):
        return False
    # SET NX EX: one refresher across all workers; the TTL frees the lock if we crash.
    if not redis_client.set(LOCK_KEY, "1", nx=True, ex=900):
        return False
    try:
        refresh(session, researcher, data_dir)
        return True
    finally:
        redis_client.delete(LOCK_KEY)


def trend_fit(trend: Trend, palette, styles: list[str]) -> dict | None:
    """Does this trend suit you? Deterministic: your colour palette and your styles, no model call.

    Returns {"verdict": "suits" | "adapt" | "neutral", "notes": [...]}, or None without an analysis.
    """
    from .colours import families, family

    if not palette.personal:
        return None
    names = [c["name"] for c in trend.colours or []]
    good = [n for n in names if family(n) in palette.good]
    bad = [n for n in names if family(n) in palette.bad and family(n) not in families(good)]
    notes = []
    if good:
        verdict = "suits"
        notes.append(f"{good[0].capitalize()} is in your colours")
    elif bad:
        verdict = "adapt"
        swap = next((c for c in palette.colours if family(c) not in families(names)), palette.colours[0])
        notes.append(f"{bad[0].capitalize()} isn't your best colour near the face: try it in {swap}")
    else:
        verdict = "neutral"
        notes.append("Neutral for your colouring")
    if trend.style_id in styles:
        notes.append("Matches your style")
    return {"verdict": verdict, "notes": notes}
