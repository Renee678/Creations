# Image evaluation

A small labelled set of outfit photos, scored automatically, so the AI's accuracy is a number rather than an
impression. The table it writes (`evals/results.md`) is ready to paste into `AI_WORKFLOW.md`.

## Add a photo

1. Put the photo in `evals/photos/` (git ignores it, so screenshots stay on your computer).
2. Add one row to `evals/cases.csv` for **each piece** you expect to be found in it. Leave a column blank to
   skip scoring it.

| Column | What to write | Example |
|---|---|---|
| `image` | the file name in `evals/photos/` | `xhs-01.jpg` |
| `category` | `top`, `bottom`, `dress`, `outerwear`, `shoes`, `bag` or `accessory` | `bottom` |
| `subtype` | top: `cardigan`, `hoodie`, `tee`, `tank`, `bodysuit`, `corset`, `knit`, `shirt`; bottom: `skirt`, `shorts`, `leggings`, `jeans`, `trousers`; dress: `dress`, `jumpsuit`; outerwear: `blazer`, `trench`, `gilet`, `coat`, `jacket`; shoes: `boots`, `sneakers`, `sandals`, `heels`, `flats`; bag: `backpack`, `clutch`, `tote`, `crossbody`, `shoulder` | `skirt` |
| `colour` | the colour in plain words (scored by colour family) | `cream` |
| `length` | skirts and dresses only: `mini`, `midi` or `maxi` | `midi` |
| `pattern` | `yes` for printed, striped, checked; `no` for plain | `no` |
| `partial` | `yes` if the piece is cut off at the photo's edge | `no` |
| `notes` | anything worth remembering | `XHS screenshot, busy background` |

Two synthetic example photos are included so the harness runs out of the box. Their expected rows describe
what the offline model returns for them; delete them once you add real photos.

## Run it

```bash
python scripts/run_eval.py            # uses Claude when ANTHROPIC_API_KEY is in .env, else the offline model
python scripts/run_eval.py --fake     # offline, to check the harness itself
```

Locally it uses the app's own settings (`.env`): the database and catalog the app uses. On a new database it
imports `CATALOG_SOURCE` first (`CATALOG_SOURCE=seed` is quick; the real catalog takes a while).

On the server, where the real catalog already is, copy the set in and the results back:

```bash
docker compose cp evals api:/app/evals
docker compose exec api python -m lookmate.eval
docker compose cp api:/app/evals/results.md evals/results.md
```

### Try-on identity (optional)

```bash
python scripts/run_eval.py --tryon --person evals/photos/me.jpg
```

For each photo it dresses `--person` (a full-body photo, or the My model image) in the top dupe of every piece,
the way the fitting room does, and saves the picture in `evals/out/`. Check each by eye and fill in the
"Same face and hair?" column. It needs `REPLICATE_API_TOKEN` and costs one Nano Banana call per photo.

## What it measures

- **Category recall**: labelled pieces the model found (a detected item of the same category).
- **Attribute accuracy**: of the pieces found, how often the garment type, colour family, length, pattern
  and the "cut off" flag match the label. Scored with the same rules the dupes search uses
  (`services/match.py`), so a "blouse" counts as `shirt` and "ivory" as the white family.
- **Dupes precision@5**: of the top 5 dupes shown for each found piece, the share that is right for the
  *labelled* piece (same type, colour family, length and pattern). Wrong perception shows up here as wrong dupes.
- **Pieces with no dupes shown**: the share of found pieces where the search showed nothing rather than a
  near-miss.
- **Extra items**: detected items with no label (possibly real pieces you didn't label).
- **Latency**: perception plus search per photo, median and p95.

## Lookbook evaluation ("Create my looks")

The Lookbook starts from a selfie: the AI reads your colour season, then builds outfits in your colours. This
checks both steps on a folder of selfies of **one person** (you), so it needs no answer file.

1. Put 15–25 selfies in `evals/lookbook-photos/` (git ignores it). Vary the light: indoor, outdoor, day, night,
   with and without makeup; a few with filters show whether the AI is fooled.
2. Run it:

```bash
python scripts/run_lookbook_eval.py                                   # writes evals/lookbook-results.md
python scripts/run_lookbook_eval.py --season summer --undertone cool  # if you know your colour season
```

What it measures:

- **Consistency**: the share of photos that get the most common colour season, sub-season and undertone. One
  person should get the same answer in every photo; a drop shows how much lighting and filters sway it.
- **Accuracy** (only with `--season` / `--undertone`): the share that matches your known answer.
- **Outfits**: for each photo, the current season's lookbook is built the way "Create my looks" builds it,
  stylist included. It reports how many outfits the AI stylist approved, pieces in a colour the analysis said to
  avoid, and outfits with more than one bright piece (both should be 0%).
- **Would you wear them?**: a blank column in the per-photo table for your own judgement.

Each photo costs two Claude calls (analysis and stylist).
