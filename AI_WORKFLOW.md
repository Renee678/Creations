# AI Workflow

> Written with Claude Code from the development log, with first-hand notes from Renee.

## What the project does

Lookmate turns outfit inspiration from social media into affordable look-alikes. A user uploads
a screenshot from Xiaohongshu, TikTok or Instagram. A vision model breaks the outfit into items, and the
app finds cheaper alternatives in a product catalog. Results are ranked by similarity, the user's body
shape, budget and a style memory that learns from every upload and save. A weekly job researches current
trends (old money, coquette, clean girl …) and links each one to catalog items.

Under the hood: an Nginx API gateway (rate limits, upload cap, request IDs), a FastAPI service, a reliable
Redis job queue with a worker (at-least-once delivery, idempotent handlers, backoff retries, crash
recovery), Postgres, and an in-memory vector index. It runs with one `docker compose up`, with or without
an API key.

## Why this project

- **Relevant to SHEIN.** SHEIN's model depends on turning fast-moving demand into orders. Social platforms
  create the demand, but the step from "I like this look" to "I bought something like it" is
  broken. Price-led "dupe" culture on Xiaohongshu fits SHEIN's low-price positioning.
- **Not just photo search.** Taobao's photo search and SHEIN's own Camera Search already find *identical* items. This
  project focuses on what they don't do: personalisation (body shape, budget, learned style), explanations
  for each pick, and trend context.
- **Real system-design and SRE content.** Slow, costly LLM calls behind a public endpoint force decisions
  about async processing, retries, idempotency, rate limiting, privacy and graceful degradation.
- **I would use it myself.** Especially My Style: I can gather every outfit I love into sets and keep them in
  one little book of my own, and whenever I find something new I like, I just add it in.

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
| **Inside the app:** Claude (`claude-opus-5-5`) via the Anthropic SDK | Outfit image → structured items (`messages.parse` with a Pydantic schema); weekly trend research with the web search tool; the lookbook stylist, which picks each outfit from pre-scored candidates |

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
   tests with every change, retrieval and scoring deterministic). Tests enforce the rules that matter.

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
the last good batch or to seed data on any failure. On the live server with a real key it worked: the Trends
tab shows researched trends with their source links, not the seed. Live testing still caught a quality gap:
colour-trend cards showed the top search hits whatever their type or colour (bags and beige dresses under
"tomato red" and "aqua"), so a colour card now keeps only clothes in the trend's colours, with a test.

**5. Rules alone had no taste.** The first lookbook was fully rule-based: fixed outfit formulas, each slot
filled by vector search and a score, the LLM only reading my photos. Testing it, I got a "Quiet luxury
spring" outfit (in autumn) that paired a bright green satin blazer with bright blue faux-leather trousers.
Claude traced it to the code: slots were filled independently, nothing judged the outfit as a whole, a
"navy trousers" search matched bright blue, and blue still counted as "in my palette". The fix has two
layers. Deterministic rules got stricter (one accent per outfit; neutral slots accept only true neutrals and
reject words like neon or metallic; muted styles reject loud pieces; the current season comes first), and
these are unit-tested with the exact failing case. On top, Claude now acts as a stylist: it sees the top 5
scored candidates per slot plus a written definition of each style, picks the most cohesive combination and
says why in one line. Its answer is validated (an id that wasn't offered keeps the scorer's pick), cached,
capped per day, and on any error the scorer's picks stand. The project rule in `CLAUDE.md` changed from
"the LLM only perceives" to "retrieval and scoring stay deterministic; Claude curates the final outfit from
scored candidates, with a deterministic fallback". In the lookbook evaluation below the stylist approved 93%
of 75 outfits, and across 165 pieces none was in a colour to avoid and no outfit had two bright pieces.

## How generated code was evaluated

- **Tests as the contract.** Over 290 unit and integration tests run fully offline (SQLite, fakeredis, a
  deterministic fake vision model), covering ranking, fit rules, the queue's failure modes, idempotent
  uploads, style memory, trends and input validation.
- **End-to-end smoke test** (`scripts/smoke_test.sh`) against the real Compose stack: Postgres, Redis,
  worker and gateway. CI runs it on every push.
- **LLM output is never trusted raw.** The vision model must return a Pydantic schema. Anything
  downstream depends only on validated fields, and refusals or invalid output become explicit failures.
- **Deterministic where possible.** Prices, fit guidance, ranking and the style profile are rule-based, so
  their behaviour can be tested exactly. The LLM perceives (photos, trends) and, for lookbooks, only
  chooses among candidates the rules already scored, with a rule-based fallback.
- **Browser tests.** A Playwright suite (`tests/e2e`) replays every bug I reported while clicking through the
  app; it runs offline in CI and against the deployed site (`LOOKMATE_URL=... make e2e`).
- **Evaluation on my own photos** (live server, real catalog, Claude; scripts `scripts/run_eval.py` and
  `scripts/run_lookbook_eval.py`, full tables in `evals/`):
  - *Find dupes, 24 outfit photos, 68 hand-labelled pieces:* every piece found (100% category recall); 18
    attribute mistakes, mostly garment type (tank tops read as "halter") and the "cut off at the edge" flag;
    88% of shown dupes had the right type, colour, length and pattern (90 of 102); none outside the price
    range; 5-7 s per photo. Photos with no exact match show an honest "not in our catalog" note by design.
  - *Lookbook, 25 photos of me:* the colour season was stable (winter on 21 of 25), the sub-season was not
    (cool winter on 11 of 25), and the undertone was cool every time it was read. Warm indoor light mattered
    more than a clear face. The AI's own photo check judged framing well (96%) but called warm-lit photos
    "good for colour" too often (52%), so its written caveats are more useful than its yes/no flag.
    About 27 s per photo for analysis plus lookbook.
  - *What the numbers missed:* clicking through found matching bugs the labels didn't cover, each fixed with
    a test: light-blue jeans matched nothing (catalog jeans name their colour by wash, a "Jeans & Jeggings"
    label read every jean as skinny, and "cropped ankle length" required the word "cropped"), a bracelet
    returned earrings, and a re-uploaded photo kept results from the old rules. A read-only diagnostic
    (`python -m lookmate.why_no_dupes`) now shows which rule rejects each candidate.
  - *Image models, judged by eye:* try-on (FASHN) and "My model" (Nano Banana Pro) can't be scored by a
    script. On my photos the try-on copied the shop model's skin and jeans waistband into the gaps of a
    cropped cardigan, and My model came out wider than my profile and added glasses; prompts and settings
    were changed for each, and "Use my original photo" stays as the most faithful option.
  - *Which model does what, and the speed trade-off:* Claude reads photos, researches trends and curates
    outfits; FASHN dresses the person, one garment per call (tryon-v1.6 for clothes, Try-On Max for shoes);
    Nano Banana Pro on Replicate draws "My model" once per user. FASHN was chosen over Nano Banana and
    IDM-VTON for try-on because it kept the person closest to real. Garments go on one after another, so
    the detail setting multiplies: "quality" draws hands and fabric most realistically but takes close to a
    minute for a three-piece outfit, "balanced" about half that. The default is "quality" (`FASHN_MODE`
    switches it): for a fitting room, a result that looks like you is worth the wait, and the screen shows a
    running timer while it works.
  - *Try-on fixes from looking at the pictures:* our own head paste-back once blurred high necklines (the
    feathered face oval reached below the chin; it now stops at the chin, with a test). And no single FASHN
    setting suits every top: drawn over My model's clothes, a bolero-and-cami showed the old tank through its
    open front; with the old clothes removed first, a cropped cardigan got the shop model's jeans waistband
    painted into the gap. So the choice is made per garment: open-front, bolero, shrug, kimono and two-in-one tops
    take the old top off first, everything else is drawn over it, and a test pins both. Two more came from the
    wearing order: a cropped top now goes on before the trousers, so their waistband fills the bare waist
    (old denim-shorts fringe showed there before); and a jacket is no longer drawn over a top, because each
    step replaces the whole upper body (a blazer over a crew-neck jumper came back as one V-neck knit). The
    jacket is pinned beside the picture with the shoes and bag, and the caption says so.
