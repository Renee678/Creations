# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project
Lookmate: upload an outfit photo, get personalised cheaper look-alikes.
FastAPI + Redis job queue + worker + Postgres behind an Nginx gateway. See README.md for architecture.

## Commands
- Install: `make install` (creates `.venv`, installs `.[dev]`)
- Tests: `make test` — offline; uses SQLite, fakeredis, the hash embedder and the fake vision model
- Full stack: `docker compose up --build -d`, then `./scripts/smoke_test.sh`
- No Docker: `python scripts/run_local.py` (SQLite + in-memory Redis + inline worker, port 8000)
- Offline stack: `CATALOG_SOURCE=seed EMBEDDER=hash docker compose up --build -d`
- Force a trend refresh: `python -m lookmate.trends_cli`

## Rules
- Never put API keys in tracked files. Keys live only in `.env` (git-ignored); `tests/test_no_secrets.py` enforces this.
- Every behaviour change ships with a test. Run `make test` before committing.
- Commit in small, descriptive steps. Never squash or rewrite history (the assessment requires the full history).
- The LLM only does perception (image → structured attributes) and trend research. Ranking, fit rules and
  style memory stay deterministic and unit-tested.
- Anything that calls Claude must also work with `FakeVision` / no researcher, so the app runs without a key.
- Job handlers must be idempotent: the Redis queue delivers at least once.
- UI text is Simplified Chinese; code, comments and commit messages are English.
