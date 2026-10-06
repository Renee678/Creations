"""Builds the long-lived objects shared by the API process and the worker."""

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import redis

from .catalog.embedder import make_embedder
from .catalog.importer import catalog_is_current, ensure_catalog
from .catalog.service import Catalog
from .config import Settings
from sqlalchemy import inspect

from .db import Base, SessionLocal, engine
from .jobqueue import JobQueue
from .llm.client import VisionLLM, make_vision_llm
from .models import Trend
from .services.trends import ClaudeTrendResearcher
from .tryon.client import make_model_maker, make_tryon

LOOK_QUEUE = "looks"
TRYON_QUEUE = "tryons"  # its own queue: a slow render never holds up a look or an analysis
CATALOG_VERSION = "catalog:version"  # set when a background import lands, so the worker reloads too
log = logging.getLogger("lookmate.runtime")
_memory_server = None


def make_redis(url: str) -> redis.Redis:
    """`memory://` gives an in-process Redis (fakeredis) for the no-Docker local mode."""
    global _memory_server
    if url.startswith("memory://"):
        import fakeredis

        _memory_server = _memory_server or fakeredis.FakeServer()
        return fakeredis.FakeRedis(server=_memory_server)
    return redis.Redis.from_url(url)


def _drop_outdated_trends(eng) -> None:
    """Trends are a cache, so an old schema (e.g. before seasons) is dropped and rebuilt, not migrated."""
    insp = inspect(eng)
    if "trends" in insp.get_table_names() and not {"label", "season"} <= {c["name"] for c in insp.get_columns("trends")}:
        Trend.__table__.drop(eng)


def _add_new_columns(eng) -> None:
    """create_all makes new tables but never alters old ones: add nullable columns that later versions added."""
    from sqlalchemy import text

    insp = inspect(eng)
    for table in Base.metadata.sorted_tables:
        if table.name not in insp.get_table_names():
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have and col.nullable:
                with eng.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(eng.dialect)}'))


@dataclass
class Runtime:
    catalog: Catalog
    llm: VisionLLM
    queue: JobQueue
    redis: redis.Redis
    trend_researcher: object | None  # ClaudeTrendResearcher, or None offline
    data_dir: Path
    tryon: object  # ReplicateTryOn, or PreviewTryOn without a token
    tryon_queue: JobQueue | None = None
    model_maker: object | None = None  # draws "My model" (Nano Banana); None without a Replicate token
    catalog_import: dict = field(default_factory=lambda: {"running": False})
    catalog_version: bytes | None = None  # CATALOG_VERSION when this process loaded its catalog


def build_runtime(settings: Settings, redis_client: redis.Redis | None = None, import_catalog: bool = True,
                  background_import: bool = True) -> Runtime:
    """Ready to serve within seconds: a catalog that needs (re-)importing is imported on a thread.

    The first import of the full catalog can take most of an hour on a small server. Until it lands the
    site serves the catalog it already has, or the bundled seed catalog on a brand-new database.
    """
    _drop_outdated_trends(engine)
    Base.metadata.create_all(engine)
    _add_new_columns(engine)
    embedder = make_embedder(settings.embedder)
    data_dir = Path(settings.data_dir)
    stale = False
    client = redis_client or make_redis(settings.redis_url)
    version = client.get(CATALOG_VERSION)  # read before loading: at worst one extra reload, never a missed one
    with SessionLocal() as session:
        if import_catalog:
            stale = not catalog_is_current(session, embedder, settings.catalog_source)
            if stale and not background_import:
                ensure_catalog(session, embedder, settings.catalog_source, data_dir, settings.catalog_size,
                               settings.amazon_max_items)
                stale = False
            elif stale and catalog_is_empty(session):
                ensure_catalog(session, embedder, "seed", data_dir, settings.catalog_size)
                stale = not catalog_is_current(session, embedder, settings.catalog_source)
        catalog = Catalog.load(session, embedder)
    researcher = (
        ClaudeTrendResearcher(settings.anthropic_api_key, settings.llm_model) if settings.anthropic_api_key else None
    )
    rt = Runtime(
        catalog=catalog,
        llm=make_vision_llm(settings.anthropic_api_key, settings.llm_model, settings.llm_timeout_s),
        queue=JobQueue(client, LOOK_QUEUE),
        redis=client,
        trend_researcher=researcher,
        data_dir=data_dir,
        tryon=make_tryon(settings.replicate_api_token, settings.tryon_model, settings.fashn_api_key),
        tryon_queue=JobQueue(client, TRYON_QUEUE),
        model_maker=make_model_maker(settings.replicate_api_token, settings.model_gen_model),
        catalog_version=version,
    )
    rt.catalog_import["pending"] = stale  # configured catalog not in the table yet
    if stale and settings.catalog_import == "external":
        log.info("catalog %s is not imported yet; serving %d products until the importer finishes",
                 settings.catalog_source, len(catalog.index))
    elif stale:
        rt.catalog_import["running"] = True
        threading.Thread(target=import_in_background, args=(rt, settings, embedder), daemon=True,
                         name="catalog-import").start()
    return rt


def catalog_is_empty(session) -> bool:
    from sqlalchemy import select

    from .models import Product

    return session.scalar(select(Product.id).limit(1)) is None


def import_in_background(rt: Runtime, settings: Settings, embedder) -> None:
    """Import the configured catalog, then swap it in for the API and tell the worker to reload."""
    started = time.monotonic()
    log.info("importing the %s catalog in the background; serving %d products meanwhile",
             settings.catalog_source, len(rt.catalog.index))
    try:
        with SessionLocal() as session:
            ensure_catalog(session, embedder, settings.catalog_source, Path(settings.data_dir), settings.catalog_size,
                           settings.amazon_max_items)
            catalog = Catalog.load(session, embedder)
        version = str(time.time()).encode()
        rt.redis.set(CATALOG_VERSION, version)
        rt.catalog, rt.catalog_version = catalog, version
        rt.catalog_import["pending"] = False
        log.info("catalog import done: %d products in %.0f s", len(rt.catalog.index), time.monotonic() - started)
    except Exception as e:
        rt.catalog_import["error"] = str(e)[:200]
        log.exception("background catalog import failed; still serving the previous catalog")
    finally:
        rt.catalog_import["running"] = False


def watch_catalog(rt: Runtime, stop: threading.Event, every_s: float = 15.0) -> None:
    """API side, with an external importer: swap in the new catalog as soon as the importer has stored it."""
    while not stop.wait(every_s):
        try:
            reload_catalog_if_changed(rt)
        except Exception:
            log.exception("catalog reload check failed")


def reload_catalog_if_changed(rt: Runtime) -> bool:
    """Worker side: load the catalog again once the API has finished a background import."""
    version = rt.redis.get(CATALOG_VERSION)
    if version == rt.catalog_version:
        return False
    with SessionLocal() as session:
        rt.catalog = Catalog.load(session, rt.catalog.embedder)
    rt.catalog_version = version
    rt.catalog_import["pending"] = False
    log.info("catalog reloaded: %d products", len(rt.catalog.index))
    return True
