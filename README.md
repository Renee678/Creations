# Lookmate

Turn outfit inspiration from Xiaohongshu, TikTok or Instagram into **affordable look-alikes that suit you**.
Upload a screenshot: the app identifies each garment, then finds cheaper alternatives in a product catalog,
ranked by similarity, your style memory, your body shape and your budget, and explains every pick.

| | |
|---|---|
| **Find look-alikes** | Photo → Claude vision → per-item search → personalised ranking with reasons |
| **Style memory** | Every upload and save updates your style profile, which feeds back into ranking |
| **Profile & fit** | Height, weight, age, body shape, preferred styles, budget → rule-based fit guidance |
| **Trend radar** | A weekly job researches current styles (老钱风, 千金风, …) with Claude web search and links each trend to catalog items |

## Quick start

Requirements: Docker with Compose v2.

```bash
cp .env.example .env          # optional: add ANTHROPIC_API_KEY for real image analysis
docker compose up --build -d  # first boot downloads the H&M catalog (~250 MB) and embedding model
open http://localhost:8080
```

**No API key?** It still runs end to end: a deterministic fake vision model stands in for Claude.
**Fully offline** (no downloads; small bundled catalog):

```bash
CATALOG_SOURCE=seed EMBEDDER=hash docker compose up --build -d
```

## Tests

```bash
make install && make test        # unit + integration tests, offline (SQLite, fakeredis, fake LLM)
./scripts/smoke_test.sh          # end-to-end against a running stack, through the gateway
```

CI (GitHub Actions) runs the test suite and boots the full Compose stack for the smoke test on every push.

## Architecture

```
browser ──► Nginx gateway ──► FastAPI (api) ──► Postgres  (users, looks, style events, trends, catalog)
            rate limits,          │   ▲
            upload cap,           ▼   │ results
            request IDs         Redis queue ──► worker ──► Claude (vision, web search)
                                                  └──────► in-memory vector index (catalog)
```

| Component | Role |
|---|---|
| `gateway/nginx.conf` | Single entrypoint. Per-IP rate limits (tighter on uploads, which cost LLM money), 8 MB upload cap, request IDs, timeouts |
| `src/lookmate/api/` | REST API: profiles, looks, style memory, trends; also serves the web app |
| `src/lookmate/worker.py` | Processes look jobs; schedules the weekly trend refresh |
| `src/lookmate/jobqueue.py` | Reliable Redis queue: atomic reserve (BLMOVE), ack, delayed retries, crash recovery |
| `src/lookmate/llm/` | Claude vision client with structured output, plus the offline fake |
| `src/lookmate/catalog/` | Catalog import, embeddings, exact vector search |
| `src/lookmate/services/` | Ranking, body-shape rules, style memory, trend radar |

## Key design decisions

- **Async pipeline for uploads.** Vision calls take seconds, so the API returns `202` with a job id and the
  browser polls. The queue is at-least-once (jobs survive a worker crash), so the handler is idempotent.
- **Idempotent uploads.** The same user uploading the same image (by SHA-256) gets the existing job back:
  double-clicks and retries never pay for a second LLM call.
- **Retries only for transient errors.** Rate limits, timeouts and 5xx retry with exponential backoff (max 3);
  bad requests and refusals fail immediately.
- **Photos are not kept.** The raw image is deleted as soon as analysis finishes.
- **Image → text → vector search.** Claude describes each item in catalog language; search runs on text
  embeddings (BGE-small, the same model as the H&M dataset's precomputed vectors). No model training needed.
- **Exact search in memory.** ~5k products × 384 dims is a few milliseconds with NumPy, so an ANN index
  (pgvector/HNSW) would add moving parts without a measurable gain. Revisit past ~200k items.
- **LLM for perception, rules for decisions.** Ranking weights, fit guidance and style memory are
  deterministic and unit-tested; the LLM only turns pixels into structured attributes.
- **Trends without scraping.** Xiaohongshu/TikTok/Instagram have no public API and forbid scraping, so the
  trend job uses Claude web search over public coverage, keeps the last good result on failure, and falls
  back to bundled seed trends.

## Data

- Catalog: [H&M e-commerce products](https://huggingface.co/datasets/Qdrant/hm_ecommerce_products)
  (CC BY 4.0), sampled to `CATALOG_SIZE` items from women's, Divided and men's lines.
- Prices are **synthetic** (the dataset has none): a deterministic budget price per category; see
  `catalog/pricing.py`.
- `data/seed_products.json` and `data/seed_trends.json` are small hand-written fallbacks for offline runs.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Enables Claude; empty uses the offline fake |
| `LLM_MODEL` | `claude-opus-5-5` | Model for vision and trend research |
| `CATALOG_SOURCE` | `hm` (compose) | `hm` or `seed` |
| `EMBEDDER` | `bge` (compose) | `bge` or `hash` (offline lexical) |
| `CATALOG_SIZE` | `5000` | Products sampled from the H&M dataset |
| `ACCESS_CODE` | empty | If set, image uploads require this code (protects API credits on a public deployment) |
| `DAILY_LOOK_LIMIT` | `200` | Global cap on new image analyses per UTC day; `0` disables it |

## Deploying to a server

`docker-compose.prod.yml` adds Caddy for automatic HTTPS and hides the gateway's dev port.
On a fresh Ubuntu server with the project copied to it (`scripts/deploy-from-windows.ps1 <ip>` does the
copy from Windows), run `bash scripts/deploy.sh`. It installs Docker if needed, fills in
`SITE_ADDRESS` (`<ip>.sslip.io` if you have no domain) and a random `ACCESS_CODE`, starts the stack,
and prints the URL and access code.
