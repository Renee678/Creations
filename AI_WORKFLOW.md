# AI Workflow

> By Renee. I own the idea, the product and every decision in it; Claude Code was my engineering partner.

## In short

- **My idea:** after researching and brainstorming with Claude, I decided to build a personal Look Book you
  shop from, not another search by picture or by item: a personal reference that tells you which colours,
  hairstyles and trends suit you, and keeps the outfits you love.
- **My part, as product owner and AI engineer:**
  - *System design:* from Claude's options I chose the architecture: an async Redis job queue and worker so
    slow model calls never block a request, deterministic retrieval and scoring with the LLM limited to
    perception and curation behind a schema, and one Compose stack that degrades gracefully without keys.
  - *Model selection:* I compared try-on models (Nano Banana, IDM-VTON, FASHN) on my own photos, picked FASHN
    in quality mode and Nano Banana Pro for My model, and judged every generated image.
  - *Environment and deployment:* I set up the API keys and runtime parameters (models, modes, daily quotas,
    access code), provisioned the Hetzner server and deployed and redeployed the stack with HTTPS.
  - *Evaluation and QA:* I built and labelled the test set (49 photos, 68 hand-labelled pieces), ran it on
    the live server, tested every feature by hand, and turned each bug into a regression test.
  - *Product:* which features to build or cut, and the visual style of the Look Book.
- **Claude Code's part:** it proposed options with a recommendation and wrote the code and tests; I reviewed it.
- **How it is validated:** about 300 automated tests, an evaluation on real data (45,000 products from three
  public datasets on the live server), and a rule that AI output is never trusted raw.

## What the project does

Lookmate is an AI fashion advisor. Its heart is **My Look Book**: selfies give your colour season, palette, hair
and makeup ideas; outfits you love become pages (a flat lay, or "On me" via virtual try-on) you shop from.
**Find dupes** turns an outfit screenshot into cheaper look-alikes ranked by similarity, body, budget and style.

## Why this project

- **I would use it myself.** Especially My Style: I can gather every outfit I love into sets and keep them in
  one little book of my own, and whenever I find something new I like, I just add it in.
- **Relevant to SHEIN.** Social platforms create demand, but the step from "I like this look" to "I bought
  something like it" is broken. Price-led "dupe" culture on Xiaohongshu fits SHEIN's low-price positioning.
- **Not just photo search.** Taobao's and SHEIN's own Camera Search find *identical* items. I wanted what they
  don't offer: personalisation (colours, body, budget, learned style), a reason for each pick, and a keepsake.
- **Real system-design and SRE content.** Slow, costly AI calls behind a public endpoint need a queue, retries,
  idempotency, rate limits and graceful degradation.

## Tools, models and infrastructure

I built a working demo by combining hosted AI services and standard infrastructure, not by training models.
No other AI coding assistant (Copilot, Cursor) was used.

| Tool | Used for |
|---|---|
| **Claude Code** (cloud sessions in a Claude Project), model Claude Opus 5.5 | My development partner: research with web search, options, code, tests, debugging, docs, commits |
| **Anthropic API** (Claude Opus 5.5, in the app) | Photo → structured items (Pydantic schema), colour analysis, weekly trend research with web search, the lookbook stylist |
| **FASHN API** (tryon-v1.6 for clothes, Try-On Max for shoes) | Virtual try-on on My model, one garment per call |
| **Replicate** (Google Nano Banana Pro) | Drawing "My model" once per user from one full-body photo; Nano Banana as the try-on fallback |
| **BAAI bge-small-en-v1.5** (fastembed, on CPU) | Text embeddings for catalog search, no GPU and no API call |
| **Docker Compose** | One command runs Nginx, FastAPI, the worker, Redis 7 and Postgres 16, with or without API keys |
| **Hetzner Cloud** (CPX21, Ashburn) + Caddy | The live demo server; Caddy adds HTTPS; deployed from my Windows PC with one script |
| **GitHub Actions**, pytest, Playwright | CI runs every test and boots the full stack for a smoke test on each push |

## Data: finding, comparing and choosing it

The hardest data problem was a catalog with real prices *and* product photos usable for try-on; most public
fashion datasets have one or the other. I compared candidates with Claude against three criteria (women's
fashion, a price, a usable photo), and changed course when a source failed:

| Dataset | Result | Why |
|---|---|---|
| H&M e-commerce (Qdrant, with vectors) | dropped | my first choice; its image bucket had been deleted, so no photos |
| ASOS e-commerce (UniqueData) | 2,500 kept | real prices and CDN photos; rows repeat per size, so deduplicated per SKU |
| Polyvore (Marqo, 1 of 6 shards) | 2,500 kept | designer pieces with photos; no prices, so a deterministic designer-band price |
| Amazon Reviews 2023 (UCSD), clothing metadata | 40,000 kept | real prices and photos; filtered from about 7.2 million listings |

The import is a small data pipeline: it streams Amazon's gzipped file without storing it, filters by category
path and exclusion words, deduplicates, embeds in cached chunks so a crash resumes, runs in a memory-capped
process, and swaps the new catalog in one transaction while the site keeps serving the old one.

## How I worked with AI

1. **I decide, Claude builds.** At each fork Claude laid out options with a recommendation and I chose; Claude
   wrote the module and its tests and committed in small steps. Design too: the Favourites closet took six mockups.
2. **I was the tester.** Every bug I reported with a screenshot became a fix plus a regression test (unit, or
   Playwright in `tests/e2e/test_regressions.py`), and I checked each fix live.
3. **Guardrails in the repo.** `CLAUDE.md` records my rules (no keys in tracked files, a test with every change,
   retrieval and scoring deterministic), and tests enforce the ones that matter.

## Where AI significantly helped

**1. Research that changed my direction.** My first idea was a flash-sale system. In under an hour Claude's
market research found hundreds of load-tested GitHub projects like it, and that SHEIN already ships Camera
Search, so I pivoted to personalisation and explanations (`docs/market-research.zh.md`, in Chinese).

**2. Designing the failure modes.** Claude proposed the reliable queue (atomic `BLMOVE` into a per-worker list,
delayed retries in a sorted set, recovery on startup) and wrote a test for each failure path: transient errors
retried, permanent ones failing fast, duplicate delivery harmless, a crashed worker's jobs recovered.

**3. Options to choose from, so I could decide fast.** At each fork Claude gave two or three options with their
consequences and a recommendation. When try-on drew a blazer over a jumper as one V-neck knit, Claude found why
(each step replaces the whole upper body) and offered three fixes; I chose "skip the jacket" in one step.

## Where AI output needed correction or validation

**1. An overconfident claim.** Claude first said flash-sale projects "have no load-test evidence"; checking
GitHub showed several with k6 tests. **Lesson:** I treat AI claims about "what exists" as hypotheses to check.

**2. A leaked API key.** I pushed a real key in a tracked file. Claude spotted it, I revoked it, and
`tests/test_no_secrets.py` now fails CI on any key (history stays unrewritten, as the assessment requires).

**3. Rules alone had no taste.** Testing the first, fully rule-based lookbook, I got a bright green satin blazer
with bright blue trousers labelled "quiet luxury". Claude traced it: slots were filled independently and
nothing judged the whole outfit. I decided on two layers: stricter, unit-tested rules (one accent per outfit,
true neutrals in neutral slots), then Claude as a stylist that picks among the top 5 scored candidates per
slot and says why. Its answer is validated, cached and capped, and on any error the scorer's picks stand. In
the evaluation below it approved 93% of 75 outfits, with no colour to avoid and no outfit with two bright pieces.

**4. My eyes find the error, AI finds the cause.** With hosted models and no labelled data to train on, the AI
could not tell that a picture looked wrong; I could. I sent a screenshot, Claude traced the cause and fixed it
with a rule and a test: a frayed band under a cropped cardigan was My model's shorts left at the waist; "no
light blue jeans" was a shade rule rejecting plain "blue"; a blurred neckline was our own face paste-back.

**5. Green tests, missing data.** Results felt thin while I tested. On submission day I asked why the live
catalog showed 5,000 products, not 45,000: one over-long Amazon field made Postgres reject the whole import,
while the SQLite tests never check lengths. Claude reproduced it on a real Postgres and fixed it the same hour.

## How generated code was evaluated

- **Tests as the contract.** 266 unit and integration tests run offline (SQLite, fakeredis, a fake vision
  model): ranking, fit and colour rules, the queue's failure modes, idempotent uploads, style memory, try-on
  steps. 34 Playwright browser tests replay every bug I reported, offline in CI or against the live site.
- **Smoke and load tests.** `scripts/smoke_test.sh` runs against the real Compose stack in CI;
  `scripts/load_test.py` measures latency and the gateway's rate limiting on free endpoints.
- **AI output is never trusted raw.** Vision output must match a Pydantic schema; the stylist may only choose
  candidates the rules already scored; a try-on never draws a garment it has no real photo of.
- **Evaluation on my own photos** (I labelled them; live server, real catalog; `scripts/run_eval.py`,
  `scripts/run_lookbook_eval.py`, tables in `evals/`):
  - *Catalog:* the numbers below were measured on the first 5,000 products (ASOS and Polyvore; see
    correction 5). The live catalog now holds all 45,000.
  - *Find dupes, 24 photos, 68 hand-labelled pieces:* every piece found (100% category recall); 88% of shown
    dupes had the right type, colour, length and pattern (90 of 102); none outside the price range; 5-7 s
    per photo. Most mistakes were garment type (tank tops read as "halter").
  - *Lookbook, 25 photos of me:* the colour season was stable (winter on 21 of 25), the sub-season was not
    (cool winter on 11 of 25), and warm indoor light mattered more than a clear face. About 27 s per photo.
  - *What the numbers missed:* clicking through, I found bugs the labels didn't cover (jeans by wash name, a
    bracelet returning earrings, a re-upload keeping old results), each fixed with a test.
    `python -m lookmate.why_no_dupes` shows which rule rejects each candidate.
  - *Image models, judged by my eye:* no script can score try-on, so I compared providers on my own photos;
    FASHN kept me closest to real. Its quality mode takes about a minute per outfit, shown with a timer.

## What I learned, and what I would do next

- **AI made building cheap, so judgement became the bottleneck.** The hard parts were deciding what the product
  is, which result looks right and which claim to doubt. That is where I spent my time.
- **Next:** label more photos so the evaluation covers more garment types, measure whether users keep coming
  back to their Look Book, and add a try-on model that can layer a jacket over a top.
