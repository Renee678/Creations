# Lookmate

Turn outfit inspiration from Xiaohongshu, TikTok or Instagram into **affordable look-alikes that suit you**.
Upload a screenshot: the app identifies each garment, then finds cheaper alternatives in a product catalog,
ranked by similarity, your style memory, your body shape and your budget, and explains every pick.
Or start from yourself: share a selfie and the app reads your colour season, face shape and best colours,
suggests hair and makeup, and builds a personal lookbook for every season or occasion from shoppable items.

| | |
|---|---|
| **Find look-alikes** | Photo → Claude vision → per-item search → personalised ranking with reasons; a sporty piece never stands in for a tailored one |
| **Personal lookbook** | 1-3 photos of you → colour season, palette, face shape, hair and makeup ideas → complete outfits for this season (or the next) or per occasion (work, weekend, date night, party, vacation), curated by an AI stylist with a one-line "why it works" |
| **Make it mine** | In the Lookbook, start from a vibe ("quiet luxury autumn") or an inspiration photo: each piece is rebuilt in your colours and budget, shown as "original → your version" with the reason (e.g. camel → charcoal for a cool winter) |
| **Virtual try-on** | Mix pieces from any lookbook outfit in the fitting room and "Try it on me" renders them on your own full-body photo (Nano Banana on Replicate by default); without a token it shows a collage of you next to the pieces |
| **Outfit boards** | Every lookbook outfit is shown as a flat-lay board (each piece placed and labelled, Xiaohongshu style). Save it, or a mix from the fitting room, to **My outfits** in My Style, filtered by season and style |
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
- **Try-on uses a hosted model, not a trained one.** By default Google's Nano Banana, an official model on
  Replicate: always warm, and one call dresses the whole outfit, shoes and bags included. The open-source
  [IDM-VTON](https://github.com/yisol/IDM-VTON) (`TRYON_MODEL=cuuupid/idm-vton`) is cheaper, but as a community
  model that has gone idle it can take minutes to boot (it was the first choice and made the demo look stuck),
  so a step is cancelled after 4 minutes rather than retried and paid twice. [FASHN](https://docs.fashn.ai)
  (`FASHN_API_KEY`) is the specialist option. IDM-VTON and FASHN swap one garment per call, so the worker renders the bottom, then one
  upper-body piece (or a dress), feeding each output into the next call; shoes, bags and accessories are pinned
  beside the picture instead. The job is queued like the others (retries, idempotent by photo + outfit, its own
  daily cost cap). The full-body photo is sent to the try-on service, dropped from Lookmate after rendering, and the
  result has an unguessable id and a delete button.
- **Image → text → vector search.** Claude describes each item in catalog language; search runs on text
  embeddings (BGE-small, computed locally with fastembed). No model training needed.
- **Exact search in memory.** ~5k products × 384 dims is a few milliseconds with NumPy, so an ANN index
  (pgvector/HNSW) would add moving parts without a measurable gain. Revisit past ~200k items.
- **LLM for perception and taste, rules for retrieval and scoring.** Ranking weights, fit guidance, colour
  rules and style memory are deterministic and unit-tested; the LLM turns pixels into structured attributes
  and, in the lookbook, chooses among candidates the rules already scored.
- **Lookbooks: rules shortlist, a stylist chooses.** Outfits come from fixed formulas for the current season
  (the next one is a tap away) or an occasion, flavoured by the user's styles and current trends, coloured
  from their palette, and filled by catalog search with a transparent score. One slot per outfit carries the
  accent colour; the others accept only true neutrals (black, white, grey, cream, navy, plus camel and brown
  for warm palettes) and never words like neon or metallic, so two loud pieces can't meet. Rules alone have
  no taste, though, so Claude then sees the top 5 candidates per slot and a written definition of each style
  (`STYLE_DEFINITIONS`), picks the most cohesive combination and says why in one line. It is also the quality gate: an outfit it
doesn't approve (clashing colours, a piece off-style) is not shown. One call per lookbook
  page, cached for a week, capped by `DAILY_STYLIST_LIMIT`. Its answer is validated against the candidates,
  and on any error, without a key or before a photo is analysed, the rule-checked picks stand, labelled as not
reviewed. Colour families
  map the model's words ("dusty rose") and catalog names ("Light Pink") onto one vocabulary, so palette
  matching is a set lookup that can be unit-tested.
- **Trends without scraping.** Xiaohongshu/TikTok/Instagram have no public API and forbid scraping, so the
  trend job uses Claude web search over public coverage, keeps the last good result on failure, and falls
  back to bundled seed trends.

## Data

The catalog mixes public datasets, women's fashion only. ASOS and Polyvore share `CATALOG_SIZE`; Amazon
adds its own `AMAZON_MAX_ITEMS` on top:

- [ASOS e-commerce products](https://huggingface.co/datasets/UniqueData/asos-e-commerce-dataset)
  (CC BY-NC-ND 4.0, used unmodified for a non-commercial assessment): affordable pieces with **real prices**
  (GBP converted at a fixed 1.27) and product photos hot-linked from the ASOS image CDN. Rows repeat per
  size, so the import keeps one per SKU and reads the category from ASOS's own label ("Coats & Jackets by …").
- [Polyvore](https://huggingface.co/datasets/Marqo/polyvore) (Apache 2.0, one of six shards): designer
  pieces from Polyvore outfits. The photos ship inside the dataset, so the import saves the chosen ones
  to `data/cache/images` and the app serves them at `/catalog-images/`. The dataset has no prices, so
  designer pieces get a deterministic **synthetic** price in a designer band; see `catalog/pricing.py`.
- [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/) (McAuley Lab, UCSD), item metadata for
  `Clothing_Shoes_and_Jewelry`: the import streams the gzipped file, decompressing as it reads, and keeps
  women's apparel, shoes and bags with a title, a **real price** and a photo, up to `AMAZON_MAX_ITEMS`
  (default 40,000). It drops menswear, kids, jewellery, costumes and lingerie using Amazon's category path
  and the same exclusion words as the other sources. The dataset is published for research and states no
  licence. Lookmate uses it unmodified for a non-commercial assessment, and hot-links photos from the
  Amazon image CDN rather than copying them. Cards for these pieces also link to the Amazon listing
  (`/dp/<asin>`). The filtered list is cached in `data/cache/amazon_rows_*.jsonl.gz`, and embeddings are
  cached in chunks in `data/cache/embeddings/`, so a failed or repeated import resumes instead of
  starting over.
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
| `CATALOG_SOURCE` | `asos,polyvore,amazon` (compose), `asos,polyvore` (local mode) | Comma-separated mix of `asos`, `polyvore`, `amazon`, `hm`, `seed` |
| `EMBEDDER` | `bge` (compose) | `bge` or `hash` (offline lexical) |
| `CATALOG_SIZE` | `5000` | Products from ASOS, Polyvore and H&M, split evenly across them |
| `AMAZON_MAX_ITEMS` | `40000` | Products from the Amazon source, on top of `CATALOG_SIZE` |
| `REPLICATE_API_TOKEN` | empty | Rendered try-on on Replicate; empty (and no FASHN key) shows a collage preview |
| `TRYON_MODEL` | `google/nano-banana` | Replicate try-on model: Nano Banana (warm, one call per outfit) or `cuuupid/idm-vton` (cheaper, slow cold starts) |
| `FASHN_API_KEY` | empty | Use FASHN's specialist try-on API instead (seconds per garment, about $0.075 an image) |
| `DAILY_TRYON_LIMIT` | `30` | Global cap on rendered try-ons per UTC day; `0` disables it |
| `ACCESS_CODE` | empty | If set, image uploads require this code (protects API credits on a public deployment) |
| `DAILY_LOOK_LIMIT` | `200` | Global cap on new image analyses per UTC day; `0` disables it |
| `DAILY_STYLIST_LIMIT` | `200` | Global cap on lookbook stylist calls per UTC day (cached pages are free); past it the top-scored outfits are shown |

## Cost and limits

Lookmate calls Claude Opus 5.5 once per photo analysis: roughly $0.05–0.12 per outfit photo, and
$0.10–0.20 for a personal colour analysis with several photos. These are estimates from token counts
(Opus 5.5 at $4 / $20 per million input / output tokens), not a measured bill; the worker logs
`claude usage=` for every call, so real numbers can be read from `docker compose logs worker`.
The lookbook stylist is one text-only call per new lookbook page (a few cents; repeat views are cached).
Rendered try-ons are billed by the try-on service: one Nano Banana call per outfit on Replicate (a few
cents; see the model page), or per garment, at most two per outfit, on IDM-VTON (about $0.02) or FASHN
(about $0.075). Keeping a Replicate deployment warm instead would cost $3.51/h
(L40S) to $5.04/h (A100) around the clock, so it isn't used.

Guardrails for a shared demo: `ACCESS_CODE` gates every upload, `DAILY_LOOK_LIMIT` caps Claude analyses
per day across all users (50 is plenty for reviewers), `DAILY_TRYON_LIMIT` caps rendered try-ons, and
re-uploading the same photo, or re-picking a look for another price range, never calls a model again.

## Try it on your phone

Lookmate is an installable web app (PWA), so no app store is needed:

1. Open the site's link on your phone and enter the access code when asked.
2. iPhone (Safari): tap **Share → Add to Home Screen**. Android (Chrome): tap **⋮ → Add to Home screen**
   (or **Install app**).
3. Lookmate now opens full screen from its own icon. The service worker caches only the app shell
   (HTML, CSS, JS, icons); photos, results and API calls always go to the server.

## Deploying to a server

`docker-compose.prod.yml` adds Caddy for automatic HTTPS and hides the gateway's dev port.
On a fresh Ubuntu server with the project copied to it (`scripts/deploy-from-windows.ps1 <ip>` does the
copy from Windows), run `bash scripts/deploy.sh`. It installs Docker if needed, fills in
`SITE_ADDRESS` (`<ip>.sslip.io` if you have no domain) and a random `ACCESS_CODE`, starts the stack,
and prints the URL and access code. It also sets `DAILY_LOOK_LIMIT=50` and `DAILY_TRYON_LIMIT=20` unless your
`.env` already has values. A 2 vCPU / 4 GB server is comfortable (the first start embeds the catalog);
1 GB servers can run out of memory during the build.
