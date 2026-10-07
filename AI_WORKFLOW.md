# AI Workflow

> Written with Claude Code from the development log, with first-hand notes from Renee.

## What the project does

Lookmate is an AI fashion advisor. Its heart is **My Look Book**: a personal style book you shop from. Selfies
give your colour season, palette, hair and makeup ideas; outfits you love are kept as pages (a flat lay, or
"On me" via virtual try-on on a saved model of you) and favourite pieces hang in a closet. Around it,
**Find dupes** turns an outfit screenshot from Xiaohongshu, TikTok or Instagram into cheaper look-alikes,
ranked by similarity, body shape, budget and a style memory; a weekly job researches trends.

Under the hood: an Nginx gateway (rate limits, upload cap, request IDs), FastAPI, a reliable Redis job queue
with a worker (at-least-once delivery, idempotent handlers, backoff retries, crash recovery), Postgres and an
in-memory vector index. It runs with one `docker compose up`, with or without API keys.

## Why this project

- **Relevant to SHEIN.** Social platforms create demand, but the step from "I like this look" to "I bought
  something like it" is broken. Price-led "dupe" culture on Xiaohongshu fits SHEIN's low-price positioning.
- **Not just photo search.** Taobao's and SHEIN's own Camera Search find *identical* items. Lookmate adds what
  they don't: personalisation (colours, body, budget, learned style), a reason for each pick, and a keepsake.
- **Real system-design and SRE content.** Slow, costly AI calls behind a public endpoint force decisions about
  async processing, retries, idempotency, rate limiting, privacy and graceful degradation.
- **I would use it myself.** Especially My Style: I can gather every outfit I love into sets and keep them in
  one little book of my own, and whenever I find something new I like, I just add it in.

The first idea was a flash-sale system. Research found hundreds of such GitHub projects, many load-tested,
so we pivoted to a product with a clearer gap (`docs/market-research.zh.md`, `docs/product-plan.zh.md`,
written in Chinese during planning; the plan changed later, e.g. plain JS instead of React).

## AI tools and models

| Tool | Used for |
|---|---|
| **Claude Code** (cloud sessions in a Claude Project), model Claude Opus 5.5 | Primary development interface: research, plan, architecture, code, tests, debugging, docs, commits |
| Claude Code web search; headless Chromium | Competitor and API research; screenshot-based UI review |
| **In the app:** Claude via the Anthropic SDK | Photo → structured items (Pydantic schema), colour analysis, weekly trend research with web search, the lookbook stylist |
| **In the app:** FASHN; Nano Banana Pro (Replicate) | Virtual try-on, one garment per call; drawing "My model" once per user |

No other AI coding assistant (Copilot, Cursor) was used.

## How AI fit into the workflow

1. **Research before code.** Claude mapped the assessment PDF to the idea, surveyed products and open-source
   projects, and wrote a market report and plan. I redirected it twice before any code was written.
2. **Small, verified steps.** Module, tests, `pytest`, commit; the history shows the order.
3. **A feedback loop with me as the tester.** I clicked through the live site and reported what looked wrong;
   every bug became a fix plus a regression test (unit, or Playwright in `tests/e2e/test_regressions.py`).
4. **Guardrails in the repo.** `CLAUDE.md` records the rules (no keys in tracked files, a test with every change,
   retrieval and scoring deterministic), and tests enforce the ones that matter.

## Where AI significantly helped

**1. Research that changed the project.** In one session Claude found that both halves of my first idea were
crowded, and that SHEIN already ships Camera Search, which pushed the design towards personalisation and
explanations. By hand this would have taken most of a day; it took under an hour and set the scope.

**2. Designing the failure modes.** Claude proposed the reliable queue (atomic `BLMOVE` into a per-worker list,
delayed retries in a sorted set, recovery on startup) and wrote a test for each failure path: transient errors
retried, permanent ones failing fast, duplicate delivery harmless, a crashed worker's jobs recovered.

**3. Options, not orders, for engineering decisions.** At each real fork Claude gave two or three options, each
with its consequence and one recommendation, and I chose. Example: try-on drew a blazer over a jumper as one
V-neck knit, because each try-on step replaces the whole upper body. Claude offered "skip the jacket", "draw
the jacket instead" or "document it as a limit" and recommended the first; I picked it, and the code, a test
and this document changed in one step. Decisions came fast, and the reasoning is recorded.

## Where AI output needed correction or validation

**1. An overconfident claim.** Claude first said flash-sale projects "have no load-test evidence"; checking
GitHub showed several with k6 tests. **Lesson:** treat AI claims about "what exists" as hypotheses to check.

**2. A leaked API key.** I pasted a real key into a tracked file and pushed it. Claude spotted it, had me revoke
it and added `tests/test_no_secrets.py`, which fails CI on any key. History was not rewritten (the
assessment forbids it), so revoking was the real fix.

**3. Rules alone had no taste.** The first lookbook was fully rule-based and paired a bright green satin blazer
with bright blue trousers as "quiet luxury". Claude traced it: slots were filled independently and nothing
judged the whole outfit. The fix has two layers. Stricter, unit-tested rules (one accent per outfit, true
neutrals in neutral slots); then Claude as a stylist that picks among the top 5 scored candidates per slot and
says why. Its answer is validated, cached and capped, and on any error the scorer's picks stand. In the
evaluation below it approved 93% of 75 outfits, with no colour to avoid and no outfit with two bright pieces.

**4. Human eyes find the error, AI finds the cause.** We had no labelled dataset, no time to train or
fine-tune, and used the hosted models as they are, so the AI could not tell on its own that a picture looked
wrong. The loop that worked: I sent a screenshot of what looked wrong; Claude traced it to a cause in our code
and fixed it with a deterministic rule and a regression test. A frayed band under a cropped cardigan was My
model's denim shorts left at the bare waist (fix: the cropped top goes on before the trousers); "no light blue
jeans" was a shade rule that rejected plain "blue"; a blurred high neckline was our own face paste-back
reaching below the chin. My judgement found the problems; Claude made the fixes fast and kept them fixed.

## How generated code was evaluated

- **Tests as the contract.** 265 unit and integration tests run offline (SQLite, fakeredis, a fake vision
  model): ranking, fit and colour rules, the queue's failure modes, idempotent uploads, style memory, try-on
  steps. 34 Playwright browser tests replay every bug I reported, offline in CI or against the live site.
- **Smoke and load tests.** `scripts/smoke_test.sh` runs against the real Compose stack in CI;
  `scripts/load_test.py` measures latency and the gateway's rate limiting on free endpoints.
- **AI output is never trusted raw.** Vision output must match a Pydantic schema; the stylist may only choose
  candidates the rules already scored; a try-on never draws a garment it has no real photo of.
- **Evaluation on my own photos** (live server, real catalog; `scripts/run_eval.py`,
  `scripts/run_lookbook_eval.py`, tables in `evals/`):
  - *Find dupes, 24 photos, 68 hand-labelled pieces:* every piece found (100% category recall); 88% of shown
    dupes had the right type, colour, length and pattern (90 of 102); none outside the price range; 5-7 s
    per photo. Most mistakes were garment type (tank tops read as "halter").
  - *Lookbook, 25 photos of me:* the colour season was stable (winter on 21 of 25), the sub-season was not
    (cool winter on 11 of 25), and warm indoor light mattered more than a clear face. About 27 s per photo.
  - *What the numbers missed:* clicking through found bugs the labels didn't cover (jeans by wash name, a
    bracelet returning earrings, a re-upload keeping old results), each fixed with a test.
    `python -m lookmate.why_no_dupes` shows which rule rejects each candidate.
  - *Image models, judged by eye:* try-on and My model can't be scored by a script. FASHN was chosen over
    Nano Banana and IDM-VTON because it kept the person closest to real; its "quality" mode takes close to a
    minute per outfit, worth it for a fitting room, with a timer on screen. Known limits stay visible: a
    jacket over a top isn't drawn and is pinned beside the picture, and the caption says so.
