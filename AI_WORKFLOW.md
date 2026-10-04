# AI Workflow

> Draft written with Claude Code from the development log; sections marked **TODO(Renee)** need first-hand notes before submission.

## What the project does

StyleBuddy (平替搭子) turns outfit inspiration from social media into affordable look-alikes. A user uploads
a screenshot from Xiaohongshu, TikTok or Instagram. A vision model breaks the outfit into items, and the
app finds cheaper alternatives in a product catalog. Results are ranked by similarity, the user's body
shape, budget and a style memory that learns from every upload and save. A weekly job researches current
trends (老钱风, 千金风, clean girl …) and links each one to catalog items.

Under the hood: an Nginx API gateway (rate limits, upload cap, request IDs), a FastAPI service, a reliable
Redis job queue with a worker (at-least-once delivery, idempotent handlers, backoff retries, crash
recovery), Postgres, and an in-memory vector index. It runs with one `docker compose up`, with or without
an API key.

## Why this project

- **Relevant to SHEIN.** SHEIN's model depends on turning fast-moving demand into orders. Social platforms
  create the demand ("种草"), but the step from "I like this look" to "I bought something like it" is
  broken. Price-led "平替" (dupe) culture on Xiaohongshu fits SHEIN's low-price positioning.
- **Not just photo search.** Taobao (拍立淘) and SHEIN's own Camera Search already find *identical* items. This
  project focuses on what they don't do: personalisation (body shape, budget, learned style), explanations
  for each pick, and trend context.
- **Real system-design and SRE content.** Slow, costly LLM calls behind a public endpoint force decisions
  about async processing, retries, idempotency, rate limiting, privacy and graceful degradation.
- **I would use it myself.** **TODO(Renee):** one or two sentences in your own words.

We got here through research rather than picking the first idea. The first candidate was a flash-sale
system. Market research showed hundreds of GitHub flash-sale projects, many already load-tested with k6,
and a crowded LLM-gateway space (LiteLLM, GPTCache). That led us to pivot to a product with a clearer gap
(see `docs/market-research.zh.md` and `docs/product-plan.zh.md`, written in Chinese during planning; the plan later changed — e.g. no React, and the front end is plain JS).

## AI tools and models

| Tool | Used for |
|---|---|
| **Claude Code** (cloud sessions in a Claude Project), model Claude Opus 5.5 | Primary development interface: market research, product plan, architecture, implementation, tests, debugging, docs, commits |
| Claude Code web search | Competitor and dataset research, checking API details |
| Headless Chromium driven by Claude Code | Screenshot-based UI review |
| **Inside the app:** Claude (`claude-opus-5-5`) via the Anthropic SDK | Outfit image → structured items (`messages.parse` with a Pydantic schema); weekly trend research with the web search tool |

No other AI coding assistant (Copilot, Cursor) was used.

## How AI fit into the workflow

1. **Research before code.** Claude read the assessment PDF and mapped each requirement to the idea. It
   surveyed existing products and open-source projects and wrote a market research report and a one-page
   product/architecture plan. I reviewed and redirected it twice (flash sale → combined idea → dupe finder)
   before any code was written.
2. **I own the product decisions; Claude proposes options.** For each fork (plugin vs web app, React vs
   server-rendered, scraping vs search APIs), Claude laid out trade-offs with a recommendation and I chose.
3. **Small, verified increments.** Each step was: write the module and its tests, run `pytest`, commit.
   The history shows the order: scaffold → catalog/search → profiles → async pipeline → style memory →
   trends → front end/CI.
4. **Verification beyond unit tests.** Claude ran the whole stack with Docker Compose, executed an
   end-to-end smoke test through the gateway (which also asserts that rate limiting returns `429`), and drove the UI
   in headless Chromium to review screenshots.
5. **Guardrails in the repo.** `CLAUDE.md` records project rules for Claude Code (no keys in tracked files,
   tests with every change, LLM only for perception). Tests enforce the rules that matter.

## Where AI significantly helped

**1. Market research changed the project.** Within one session Claude compared the flash-sale idea with
existing GitHub projects and commercial LLM gateways and found that both halves were already crowded.
It also pointed out that SHEIN already ships Camera Search, which pushed the design towards
personalisation and explanations rather than plain visual search. Doing this survey by hand would have
taken most of a day; here it took under an hour and directly shaped the scope.

**2. Designing the failure modes.** Claude proposed the reliable-queue design (atomic `BLMOVE` into a
per-worker processing list, delayed retries in a sorted set, recovery on startup). It also wrote tests for
each failure path: transient LLM errors retried with backoff, permanent errors failing fast, duplicate
delivery being harmless, and a crashed worker's jobs being recovered. These are the cases that usually get
skipped under time pressure.

## Where AI output needed correction or validation

**1. An overconfident claim, corrected by research.** Early on, Claude said typical flash-sale projects
"only claim no overselling and have no load-test evidence". When it actually checked GitHub, several
recent projects did have k6 load tests and reconciliation. The research report states this correction
explicitly, and the project direction changed because of it. **Lesson:** treat AI claims about "what
exists" as hypotheses until they are checked against sources.

**2. A leaked API key.** While setting up the project I pasted my real Anthropic key into `.env.example`
(a tracked file) and pushed it. Claude spotted the commit, had me revoke the key, restored the template
and added `tests/test_no_secrets.py`. That test fails CI if any tracked file contains an Anthropic key.
History was not rewritten (the assessment forbids it), so revoking the key is the actual fix.

**3. Bugs found by running the system, not by reading code.**
- Tests passed individually but failed together: the in-memory SQLite database was shared across tests.
  It was fixed with a per-test cleanup fixture.
- The first UI screenshots showed the navigation tabs and a raw file input on the onboarding page. The
  generated CSS (`display: block/flex`) overrode the HTML `hidden` attribute, and a global
  `[hidden] { display: none !important }` fixed it.
- Results showed the same garment in four colours, crowding out other designs. A `diversify()` step and a
  test were added so colour variants only fill leftover slots.

**4. Unverified API usage.** Trend research combines structured output with the server-side web search
tool. This could not be exercised without an API key in the development sandbox, so the code falls back to
the last good batch or to seed data on any failure. **TODO(Renee):** record the result of the first real
run with your key (works / needed changes).

## How generated code was evaluated

- **Tests as the contract.** Over 40 unit and integration tests run fully offline (SQLite, fakeredis, a
  deterministic fake vision model), covering ranking, fit rules, the queue's failure modes, idempotent
  uploads, style memory, trends and input validation.
- **End-to-end smoke test** (`scripts/smoke_test.sh`) against the real Compose stack: Postgres, Redis,
  worker and gateway. CI runs it on every push.
- **LLM output is never trusted raw.** The vision model must return a Pydantic schema. Anything
  downstream depends only on validated fields, and refusals or invalid output become explicit failures.
- **Deterministic where possible.** Prices, fit guidance, ranking and the style profile are rule-based, so
  their behaviour can be tested exactly. The LLM is used only where perception is needed.
- **TODO(Renee):** results of the image evaluation set (category recall, look-alike relevance) once it has run.
