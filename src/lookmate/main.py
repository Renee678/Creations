import logging
import threading
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import looks, profiles, style, trends
from .config import get_settings
from .runtime import Runtime, build_runtime
from .services.vocab import BODY_SHAPES, STYLES
from .worker import run_forever

STATIC = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.runtime = build_runtime(settings)
    stop = threading.Event()
    if settings.inline_worker:
        threading.Thread(target=run_forever, args=(app.state.runtime, stop), daemon=True, name="worker").start()
    yield
    stop.set()


app = FastAPI(title="Lookmate", lifespan=lifespan)
app.include_router(profiles.router)
app.include_router(looks.router)
app.include_router(style.router)
app.include_router(trends.router)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/vocab")
def vocab() -> dict:
    return {"styles": STYLES, "body_shapes": BODY_SHAPES, "access_code_required": bool(get_settings().access_code)}


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
