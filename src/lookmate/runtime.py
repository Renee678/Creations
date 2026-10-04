"""Builds the long-lived objects shared by the API process and the worker."""

from dataclasses import dataclass
from pathlib import Path

import redis

from .catalog.embedder import make_embedder
from .catalog.importer import ensure_catalog
from .catalog.service import Catalog
from .config import Settings
from .db import Base, SessionLocal, engine
from .jobqueue import JobQueue
from .llm.client import VisionLLM, make_vision_llm
from .services.trends import ClaudeTrendResearcher

LOOK_QUEUE = "looks"


@dataclass
class Runtime:
    catalog: Catalog
    llm: VisionLLM
    queue: JobQueue
    redis: redis.Redis
    trend_researcher: object | None  # ClaudeTrendResearcher, or None offline
    data_dir: Path


def build_runtime(settings: Settings, redis_client: redis.Redis | None = None, import_catalog: bool = True) -> Runtime:
    Base.metadata.create_all(engine)
    embedder = make_embedder(settings.embedder)
    with SessionLocal() as session:
        if import_catalog:
            ensure_catalog(session, embedder, settings.catalog_source, Path(settings.data_dir), settings.catalog_size)
        catalog = Catalog.load(session, embedder)
    client = redis_client or redis.Redis.from_url(settings.redis_url)
    researcher = (
        ClaudeTrendResearcher(settings.anthropic_api_key, settings.llm_model) if settings.anthropic_api_key else None
    )
    return Runtime(
        catalog=catalog,
        llm=make_vision_llm(settings.anthropic_api_key, settings.llm_model, settings.llm_timeout_s),
        queue=JobQueue(client, LOOK_QUEUE),
        redis=client,
        trend_researcher=researcher,
        data_dir=Path(settings.data_dir),
    )
