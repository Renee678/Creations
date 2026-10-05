# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project
Lookmate: upload an outfit photo, get personalised cheaper look-alikes.
FastAPI + Redis job queue + worker + Postgres behind an Nginx gateway. See README.md for architecture.

## Commands
- Install: `make install` (creates `.venv`, installs `.[dev]`)
- Tests: `make test` — offline; uses SQLite, fakeredis, the hash embedder and the fake vision model
- Browser tests: `make e2e` (Playwright; offline by default, `LOOKMATE_URL=https://… make e2e` for a deployed site).
  Every bug Renee reports while clicking through the app gets a test in `tests/e2e/test_regressions.py`.
- Full stack: `docker compose up --build -d`, then `./scripts/smoke_test.sh`
- No Docker: `python scripts/run_local.py` (SQLite + in-memory Redis + inline worker, port 8000)
- Offline stack: `CATALOG_SOURCE=seed EMBEDDER=hash docker compose up --build -d`
- Force a trend refresh: `python -m lookmate.trends_cli`

## Rules
- Never put API keys in tracked files. Keys live only in `.env` (git-ignored); `tests/test_no_secrets.py` enforces this.
- Every behaviour change ships with a test. Run `make test` before committing.
- Commit in small, descriptive steps. Never squash or rewrite history (the assessment requires the full history).
- The LLM does perception (image → structured attributes), trend research and outfit curation. Retrieval,
  scoring, fit rules, colour rules and style memory stay deterministic and unit-tested; Claude curates the final
  lookbook outfit only from scored candidates, with a deterministic fallback.
- Anything that calls Claude must also work with `FakeVision` / no researcher, so the app runs without a key.
- Job handlers must be idempotent: the Redis queue delivers at least once.
- Everything is English: UI text, data, docs, code, comments and commit messages.
