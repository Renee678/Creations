"""Run Lookmate without Docker: one process, SQLite, in-memory Redis, worker as a thread.

    python scripts/run_local.py        then open http://localhost:8000

Good for a laptop without virtualisation. Docker Compose remains the real deployment
(separate gateway, worker, Postgres and Redis); this mode skips the Nginx gateway.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

LOCAL_DEFAULTS = {
    "DATABASE_URL": "sqlite:///./lookmate.db",
    "REDIS_URL": "memory://",
    "INLINE_WORKER": "true",
    # ASOS (affordable, real prices) + Polyvore (designer pieces), both with product photos. The first
    # start downloads ~480 MB into data/cache; if that fails, the app falls back to the bundled catalog.
    "CATALOG_SOURCE": "asos,polyvore",
    "EMBEDDER": "bge",
}


def env_file_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line.split("=", 1)[0].strip() for line in lines if line and not line.startswith("#") and "=" in line}


# Settings in .env or the shell win; environment variables would otherwise override .env.
configured = env_file_keys(ROOT / ".env")
for key, value in LOCAL_DEFAULTS.items():
    if key not in configured:
        os.environ.setdefault(key, value)

import uvicorn  # noqa: E402

if __name__ == "__main__":
    print("Lookmate (local mode) starting: open http://localhost:8000 once it says 'Application startup complete'")
    print("The first start downloads the ASOS + Polyvore catalogs and the embedding model, which can take a few minutes.")
    uvicorn.run("lookmate.main:app", host="127.0.0.1", port=8000)
