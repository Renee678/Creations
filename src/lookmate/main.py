import logging
import threading
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import analysis, body_model, looks, profiles, style, trends, tryon
from .catalog.importer import IMAGE_ROUTE
from .config import get_settings
from .runtime import Runtime, build_runtime, watch_catalog
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
    elif settings.catalog_import == "external":
        threading.Thread(target=watch_catalog, args=(app.state.runtime, stop), daemon=True, name="catalog-watch").start()
    yield
    stop.set()


app = FastAPI(title="Lookmate", lifespan=lifespan)


@app.middleware("http")
async def revalidate_app_shell(request: Request, call_next):
    # Without this, browsers cache app.js and style.css heuristically and keep showing the old app
    # after a deploy. "no-cache" still uses the cached copy, but checks it first (a cheap 304).
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

app.include_router(profiles.router)
app.include_router(looks.router)
app.include_router(style.router)
app.include_router(trends.router)
app.include_router(analysis.router)
app.include_router(tryon.router)
app.include_router(body_model.router)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
# Product photos that ship inside a dataset (Polyvore) are saved here by the catalog import.
CATALOG_IMAGES = Path(get_settings().data_dir) / "cache" / "images"
CATALOG_IMAGES.mkdir(parents=True, exist_ok=True)
app.mount(IMAGE_ROUTE, StaticFiles(directory=CATALOG_IMAGES), name="catalog-images")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    # Served from the root so its scope covers the whole app; never cached, so updates roll out.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/vocab")
def vocab() -> dict:
    return {"styles": STYLES, "body_shapes": BODY_SHAPES, "access_code_required": bool(get_settings().access_code)}


@app.get("/healthz")
def healthz(request: Request) -> dict:
    # Healthy as soon as it serves; a catalog import still running in the background is reported, not waited on.
    rt = request.app.state.runtime
    return {"status": "ok", "catalog": {"products": len(rt.catalog.index), **rt.catalog_import}}


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
    return {"results": [{**r.product.to_dict(), "score": round(r.score, 4)} for r in results]}
