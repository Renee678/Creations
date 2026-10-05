# Lookmate

Turn outfit inspiration from Xiaohongshu, TikTok or Instagram into **affordable look-alikes that suit you**.
Upload a screenshot: the app identifies each garment, then finds cheaper alternatives in a product catalog,
ranked by similarity, your style memory, your body shape and your budget, and explains every pick.
Or start from yourself: share a selfie and the app reads your colour season, face shape and best colours,
suggests hair and makeup, and builds a personal lookbook for every season or occasion from shoppable items.

| | |
|---|---|
| **Find look-alikes** | Photo → Claude vision → per-item search → personalised ranking with reasons |
| **Personal lookbook** | 1-3 photos of you → colour season, palette, face shape, hair and makeup ideas → complete outfits per season (spring to winter) or occasion (work, weekend, date night, party, vacation) |
| **Style memory** | Every upload and save updates your style profile, which feeds back into ranking |
| **Profile & fit** | Height, weight, age, body shape, preferred styles, budget → rule-based fit guidance |
| **Trend radar** | A weekly job researches current styles (old money, coquette, …) with Claude web search and links each trend to catalog items |

## Quick start

Requirements: Docker with Compose v2.

```bash
cp .env.example .env          # optional: add ANTHROPIC_API_KEY for real image analysis
docker compose up --build -d  # first boot downloads the ASOS + Polyvore catalogs (~480 MB) and embedding model
open http://localhost:8080
```

**No API key?** It still runs end to end: a deterministic fake vision model stands in for Claude.
**Fully offline** (no downloads; small bundled catalog):

```bash
CATALOG_SOURCE=seed EMBEDDER=hash docker compose up --build -d
```

**No Docker** (e.g. a laptop without virtualisation): one process with SQLite, an in-memory Redis and
the worker as a thread. Same app and API, without the Nginx gateway:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[ml]"   # Windows: .venv\Scripts\pip
python scripts/run_local.py                                   # http://localhost:8000
```

Local mode uses the ASOS + Polyvore catalog (with product photos) by default; the first start downloads it.
Set `CATALOG_SOURCE=seed` in `.env` for the small offline catalog. Changing the source re-imports the
catalog on the next start.

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
| `src/lookmate/api/` | REST API: profiles, looks, personal analysis and lookbook, style memory, trends; also serves the web app |
| `src/lookmate/worker.py` | Processes look and personal-analysis jobs; schedules the weekly trend refresh |
| `src/lookmate/jobqueue.py` | Reliable Redis queue: atomic reserve (BLMOVE), ack, delayed retries, crash recovery |
| `src/lookmate/llm/` | Claude vision client with structured output, plus the offline fake |
| `src/lookmate/catalog/` | Catalog import, embeddings, exact vector search |
| `src/lookmate/services/` | Ranking, body-shape rules, colour families, lookbook builder, style memory, trend radar |

## Key design decisions

- **Async pipeline for uploads.** Vision calls take seconds, so the API returns `202` with a job id and the
  browser polls. The queue is at-least-once (jobs survive a worker crash), so the handler is idempotent.
- **Idempotent uploads.** The same user uploading the same image (by SHA-256) gets the existing job back:
  double-clicks and retries never pay for a second LLM call.
- **Retries only for transient errors.** Rate limits, timeouts and 5xx retry with exponential backoff (max 3);
  bad requests and refusals fail immediately.
- **Photos are not kept.** Raw images (outfits and selfies) are deleted as soon as analysis finishes.
- **Image → text → vector search.** Claude describes each item in catalog language; search runs on text
  embeddings (BGE-small, computed locally with fastembed). No model training needed.
- **Exact search in memory.** ~5k products × 384 dims is a few milliseconds with NumPy, so an ANN index
  (pgvector/HNSW) would add moving parts without a measurable gain. Revisit past ~200k items.
- **LLM for perception, rules for decisions.** Ranking weights, fit guidance and style memory are
  deterministic and unit-tested; the LLM only turns pixels into structured attributes.
- **Lookbooks are built, not generated.** The model only reads the photos (colour season, palette, face
  shape). Outfits come from fixed formulas per season or occasion, flavoured by the user's styles and current
  trends, coloured from their palette, and filled by catalog search with a transparent score. Colour families
  map the model's words ("dusty rose") and catalog names ("Light Pink") onto one vocabulary, so palette
  matching is a set lookup that can be unit-tested.
- **Trends without scraping.** Xiaohongshu/TikTok/Instagram have no public API and forbid scraping, so the
  trend job uses Claude web search over public coverage, keeps the last good result on failure, and falls
  back to bundled seed trends.

## Data

The catalog mixes two public datasets, half of `CATALOG_SIZE` each, women's fashion only:

- [ASOS e-commerce products](https://huggingface.co/datasets/UniqueData/asos-e-commerce-dataset)
  (CC BY-NC-ND 4.0, used unmodified for a non-commercial assessment): affordable pieces with **real prices**
  (GBP converted at a fixed 1.27) and product photos hot-linked from the ASOS image CDN. Rows repeat per
  size, so the import keeps one per SKU and reads the category from ASOS's own label ("Coats & Jackets by …").
- [Polyvore](https://huggingface.co/datasets/Marqo/polyvore) (Apache 2.0, one of six shards): designer
  pieces from Polyvore outfits. The photos ship inside the dataset, so the import saves the chosen ones
  to `data/cache/images` and the app serves them at `/catalog-images/`. The dataset has no prices, so
  designer pieces get a deterministic **synthetic** price in a designer band; see `catalog/pricing.py`.
- Product cards link to a SHEIN and an ASOS **search** for the piece, not to product pages: both datasets
  are snapshots and most product pages are gone. Nothing is scraped.
- `CATALOG_SOURCE=hm` still loads the older [H&M dataset](https://huggingface.co/datasets/Qdrant/hm_ecommerce_products)
  (CC BY 4.0), but its image bucket has been deleted, so those products show no photo.
- `data/seed_products.json` and `data/seed_trends.json` are small hand-written fallbacks for offline runs.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Enables Claude; empty uses the offline fake |
| `LLM_MODEL` | `claude-opus-5-5` | Model for vision and trend research |
| `CATALOG_SOURCE` | `asos,polyvore` (compose, local mode) | Comma-separated mix of `asos`, `polyvore`, `hm`, `seed` |
| `EMBEDDER` | `bge` (compose) | `bge` or `hash` (offline lexical) |
| `CATALOG_SIZE` | `5000` | Products in the catalog, split evenly across the sources |
| `ACCESS_CODE` | empty | If set, image uploads require this code (protects API credits on a public deployment) |
| `DAILY_LOOK_LIMIT` | `200` | Global cap on new image analyses per UTC day; `0` disables it |

## Deploying to a server

`docker-compose.prod.yml` adds Caddy for automatic HTTPS and hides the gateway's dev port.
On a fresh Ubuntu server with the project copied to it (`scripts/deploy-from-windows.ps1 <ip>` does the
copy from Windows), run `bash scripts/deploy.sh`. It installs Docker if needed, fills in
`SITE_ADDRESS` (`<ip>.sslip.io` if you have no domain) and a random `ACCESS_CODE`, starts the stack,
and prints the URL and access code.
