import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request

from .api import looks, profiles
from .config import get_settings
from .runtime import Runtime, build_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = build_runtime(get_settings())
    yield


app = FastAPI(title="StyleBuddy", lifespan=lifespan)
app.include_router(profiles.router)
app.include_router(looks.router)


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
    rt: Runtime = request.app.state.runtime
    results = rt.catalog.search(q, k=k, category=category, max_price=max_price)
    return {"results": [{**r.product.__dict__, "score": round(r.score, 4)} for r in results]}
