import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request

from .catalog.embedder import make_embedder
from .catalog.importer import ensure_catalog
from .catalog.service import Catalog
from .config import get_settings
from .db import Base, SessionLocal, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Base.metadata.create_all(engine)
    embedder = make_embedder(settings.embedder)
    with SessionLocal() as session:
        ensure_catalog(session, embedder, settings.catalog_source, Path(settings.data_dir), settings.catalog_size)
        app.state.catalog = Catalog.load(session, embedder)
    yield


app = FastAPI(title="StyleBuddy", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/search")
def search(
    request: Request,
    q: str = Query(min_length=1, max_length=300),
    category: str | None = None,
    max_price: float | None = Query(default=None, gt=0),
    k: int = Query(default=10, ge=1, le=50),
) -> dict:
    catalog: Catalog = request.app.state.catalog
    results = catalog.search(q, k=k, category=category, max_price=max_price)
    return {"results": [{**r.product.__dict__, "score": round(r.score, 4)} for r in results]}
