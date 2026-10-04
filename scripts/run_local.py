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

os.environ.setdefault("DATABASE_URL", "sqlite:///./lookmate.db")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("INLINE_WORKER", "true")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    print("Lookmate (local mode) starting: open http://localhost:8000 once it says 'Application startup complete'")
    uvicorn.run("lookmate.main:app", host="127.0.0.1", port=8000)
