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

Lookmate is an AI fashion advisor. Its heart is **My Look Book**: a personal style book you shop from. Selfies
give your colour season, palette, hair and makeup ideas; outfits you love are kept as pages (a flat lay, or
"On me" via virtual try-on on a saved model of you) and favourite pieces hang in a closet. Around it,
**Find dupes** turns an outfit screenshot from Xiaohongshu, TikTok or Instagram into cheaper look-alikes,
ranked by similarity, body shape, budget and a style memory; a weekly job researches trends.

## Why this project

- **I would use it myself.** Especially My Style: I can gather every outfit I love into sets and keep them in
  one little book of my own, and whenever I find something new I like, I just add it in.
- **Relevant to SHEIN.** Social platforms create demand, but the step from "I like this look" to "I bought
  something like it" is broken. Price-led "dupe" culture on Xiaohongshu fits SHEIN's low-price positioning.
- **Not just photo search.** Taobao's and SHEIN's own Camera Search find *identical* items. I wanted what they
  don't offer: personalisation (colours, body, budget, learned style), a reason for each pick, and a keepsake.
- **Real system-design and SRE content.** Slow, costly AI calls behind a public endpoint need a queue, retries,
  idempotency, rate limits and graceful degradation.

My first idea was a flash-sale system. Claude's market research found hundreds of load-tested GitHub projects
like it, so I pivoted to a clearer gap (`docs/market-research.zh.md`, `docs/product-plan.zh.md`, in Chinese).

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

## How I worked with AI

1. **I decide, Claude builds.** At each fork Claude laid out options with a recommendation; I chose the
   architecture, the features and the design. Then Claude wrote the module and its tests, ran `pytest` and
   committed in small steps. Design went the same way: Claude drew mockups, I picked and refined them over
   several rounds (the Favourites closet took six) until they looked right to me.
2. **I was the tester and reviewer.** I reported what was wrong with screenshots; every bug became a fix plus a
   regression test (unit, or Playwright in `tests/e2e/test_regressions.py`), and I checked each fix live.
3. **Guardrails in the repo.** `CLAUDE.md` records my rules (no keys in tracked files, a test with every change,
   retrieval and scoring deterministic), and tests enforce the ones that matter.

## Where AI significantly helped

**1. Research that changed my direction.** In one session Claude showed that both halves of my first idea were
crowded and that SHEIN already ships Camera Search. That pushed me towards personalisation and explanations.
By hand this would have taken most of a day; it took under an hour.

**2. Designing the failure modes.** Claude proposed the reliable queue (atomic `BLMOVE` into a per-worker list,
delayed retries in a sorted set, recovery on startup) and wrote a test for each failure path: transient errors
retried, permanent ones failing fast, duplicate delivery harmless, a crashed worker's jobs recovered.

**3. Options to choose from, so I could decide fast.** At each real fork Claude gave two or three options, each
with its consequence and a recommendation. Example: I saw try-on draw a blazer over a jumper as one V-neck
knit. Claude found why (each try-on step replaces the whole upper body) and offered "skip the jacket", "draw
the jacket instead" or "document it as a limit". I chose to skip it, and the code, a test and this document
changed in one step. Decisions came fast, and the reasoning is recorded.

## Where AI output needed correction or validation

**1. An overconfident claim.** Claude first said flash-sale projects "have no load-test evidence"; checking
GitHub showed several with k6 tests. **Lesson:** I treat AI claims about "what exists" as hypotheses to check.

**2. A leaked API key.** I pasted a real key into a tracked file and pushed it. Claude spotted it, I revoked it,
and we added `tests/test_no_secrets.py`, which fails CI on any key. History was not rewritten (the
assessment forbids it), so revoking was the real fix.

**3. Rules alone had no taste.** Testing the first, fully rule-based lookbook, I got a bright green satin blazer
with bright blue trousers labelled "quiet luxury". Claude traced it: slots were filled independently and
nothing judged the whole outfit. I decided on two layers: stricter, unit-tested rules (one accent per outfit,
true neutrals in neutral slots), then Claude as a stylist that picks among the top 5 scored candidates per
slot and says why. Its answer is validated, cached and capped, and on any error the scorer's picks stand. In
the evaluation below it approved 93% of 75 outfits, with no colour to avoid and no outfit with two bright pieces.

**4. My eyes find the error, AI finds the cause.** We had no labelled dataset, no time to train or fine-tune,
and used the hosted models as they are, so the AI could not tell on its own that a picture looked wrong. I
could. I sent a screenshot; Claude traced the cause in our code and fixed it with a rule and a regression
test. A frayed band under a cropped cardigan was My model's denim
shorts left at the bare waist (now the cropped top goes on before the trousers); "no light blue jeans" was a
shade rule that rejected plain "blue"; a blurred high neckline was our own face paste-back reaching below the
chin. My judgement found the problems; Claude made the fixes fast, and the tests keep them fixed.

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
  - *The catalog it searches:* 45,000 women's fashion products on the live server, from three public datasets
    that took real searching to find (most fashion sets lack prices or photos): ASOS and Polyvore (5,000, real
    prices and designer pieces) and Amazon Reviews 2023 (40,000, filtered from millions of listings). The
    numbers below were measured on the first 5,000 (see correction 5).
  - *Find dupes, 24 photos, 68 hand-labelled pieces:* every piece found (100% category recall); 88% of shown
    dupes had the right type, colour, length and pattern (90 of 102); none outside the price range; 5-7 s
    per photo. Most mistakes were garment type (tank tops read as "halter").
  - *Lookbook, 25 photos of me:* the colour season was stable (winter on 21 of 25), the sub-season was not
    (cool winter on 11 of 25), and warm indoor light mattered more than a clear face. About 27 s per photo.
  - *What the numbers missed:* clicking through, I found bugs the labels didn't cover (jeans by wash name, a
    bracelet returning earrings, a re-upload keeping old results), each fixed with a test.
    `python -m lookmate.why_no_dupes` shows which rule rejects each candidate.
  - *Image models, judged by my eye:* try-on and My model can't be scored by a script. I compared providers on
    my own photos and chose FASHN over Nano Banana and IDM-VTON because it kept me closest to real; its
    "quality" mode takes close to a minute per outfit, worth it for a fitting room, with a timer on screen.
    Known limits stay visible: a jacket over a top isn't drawn; it is pinned beside the picture and the
    caption says so.

## What I learned, and what I would do next

- **AI made building cheap, so judgement became the bottleneck.** The hard parts were deciding what the product
  is, which result looks right and which claim to doubt. That is where I spent my time.
- **Next:** label more photos so the evaluation covers more garment types, measure whether users keep coming
  back to their Look Book, and add a try-on model that can layer a jacket over a top.
