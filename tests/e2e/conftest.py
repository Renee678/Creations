"""Browser end-to-end tests: click through Lookmate like a person would.

Offline (default): starts the app on a free port with SQLite, in-memory Redis, the seed catalog and the fake
vision model, so it needs no keys and no network.
Live: set LOOKMATE_URL to test a deployed site instead. The access code comes from LOOKMATE_ACCESS_CODE or from
ACCESS_CODE in .env; real photos for it go in tests/e2e/photos/ (see the README there).

    pip install -e ".[e2e]" && python -m playwright install chromium
    pytest tests/e2e -v                                          # offline
    LOOKMATE_URL=https://example.com pytest tests/e2e -v           # a deployed site

A failing test saves a screenshot to test-results/.
"""

import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import httpx
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api", reason='install with: pip install -e ".[e2e]"')

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "test-results"
PHOTOS = Path(__file__).parent / "photos"
LIVE_URL = os.environ.get("LOOKMATE_URL", "").rstrip("/")


def _env_file_value(key: str) -> str:
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    return next((line.split("=", 1)[1].strip().strip("\"'") for line in lines if line.startswith(f"{key}=")), "")


# A live site's access code: from the shell, or the same ACCESS_CODE the server reads from .env.
ACCESS_CODE = os.environ.get("LOOKMATE_ACCESS_CODE") or (_env_file_value("ACCESS_CODE") if LIVE_URL else "")
TIMEOUT_MS = int(os.environ.get("LOOKMATE_TIMEOUT_S", "180" if LIVE_URL else "30")) * 1000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    if LIVE_URL:
        yield LIVE_URL
        return
    port = _free_port()
    db = Path(tempfile.mkdtemp()) / "e2e.db"
    env = os.environ | {
        "DATABASE_URL": f"sqlite:///{db}", "REDIS_URL": "memory://", "INLINE_WORKER": "true",
        "CATALOG_SOURCE": "seed", "EMBEDDER": "hash", "ACCESS_CODE": "",
        "ANTHROPIC_API_KEY": "", "REPLICATE_API_TOKEN": "", "FASHN_API_KEY": "",
        "PYTHONPATH": str(ROOT / "src"),
    }
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "lookmate.main:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            if proc.poll() is not None:
                raise RuntimeError(f"Lookmate did not start:\n{proc.stdout.read().decode(errors='replace')}")
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Lookmate did not answer /healthz within 60 seconds")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def png(rgb: tuple[int, int, int], size: int = 48) -> bytes:
    """A small solid-colour PNG; a different colour gives the fake vision model a different outfit."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def photo(name: str, rgb: tuple[int, int, int]) -> dict:
    """A test photo for a file input. Live sites read real photos, so those come from tests/e2e/photos/."""
    for ext, mime in ((".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")):
        f = PHOTOS / f"{name}{ext}"
        if f.exists():
            return {"name": f.name, "mimeType": mime, "buffer": f.read_bytes()}
    if LIVE_URL:
        pytest.skip(f"live run needs a real photo: put {name}.jpg in tests/e2e/photos/")
    return {"name": f"{name}.png", "mimeType": "image/png", "buffer": png(rgb)}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed and "page" in item.funcargs:
        RESULTS.mkdir(exist_ok=True)
        path = RESULTS / f"{item.name}.png"
        try:
            item.funcargs["page"].screenshot(path=str(path), full_page=True)
            report.sections.append(("screenshot", str(path)))
        except Exception:
            pass


def pytest_configure(config):
    config.addinivalue_line("markers", "timezone(name): run the page fixture's browser in this IANA time zone")


@pytest.fixture
def page(browser, base_url, request):
    """A fresh visitor (empty localStorage) who has filled in the profile and landed on Find dupes."""
    tz = request.node.get_closest_marker("timezone")
    context = browser.new_context(base_url=base_url, viewport={"width": 1280, "height": 900}, service_workers="block",
                                  **({"timezone_id": tz.args[0]} if tz else {}))
    if ACCESS_CODE:
        context.add_init_script(f"localStorage.setItem('accessCode', {ACCESS_CODE!r})")
    pg = context.new_page()
    pg.set_default_timeout(TIMEOUT_MS)
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept(ACCESS_CODE) if d.type == "prompt" else d.accept())
    pg.goto("/")
    form = pg.locator("#profile-form")
    form.locator("[name=nickname]").fill("e2e")
    form.locator("[name=height_cm]").fill("165")
    form.locator("[name=weight_kg]").fill("55")
    form.locator("[name=age]").fill("26")
    pg.locator("#style-options .chip").first.click()
    form.locator("button[type=submit]").click()
    pg.locator("#find").wait_for(state="visible")
    yield pg
    context.close()
