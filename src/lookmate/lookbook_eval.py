"""Evaluate the Lookbook ("Create my looks") on a folder of selfies of one person.

    python scripts/run_lookbook_eval.py                              # evals/lookbook-photos -> evals/lookbook-results.md
    python scripts/run_lookbook_eval.py --season summer --undertone cool   # also score against the known answer

For each selfie it runs the same colour analysis the app runs on upload, then builds the current season's lookbook
from it the way "Create my looks" does (stylist included). The photos are all of one person, so the colour season
should come out the same every time: agreement needs no labels. The outfit checks reuse the app's own colour rules.
Uses Claude when ANTHROPIC_API_KEY is set, else the offline FakeVision (which only proves the harness works).
"""

import argparse
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from .eval import MEDIA_TYPES, _ms, _pct, percentile
from .services.colours import NEUTRALS, family
from .services.lookbook import Palette

DENIM = re.compile(r"\b(denim|jeans?)\b", re.I)


@dataclass
class PhotoResult:
    image: str
    season: str = ""          # e.g. "summer"
    detail: str = ""          # e.g. "soft summer"
    undertone: str = ""
    caveats: str = ""
    outfits: list[dict] = field(default_factory=list)
    reviewed: int = 0         # outfits the stylist looked at (shown + rejected)
    approved: int = 0
    off_palette: list[str] = field(default_factory=list)  # pieces in a colour the analysis said to avoid
    loud: list[str] = field(default_factory=list)         # outfits with more than one bright piece
    analysis_ms: float = 0.0
    lookbook_ms: float = 0.0
    error: str = ""


def bright(piece: dict) -> bool:
    """A piece that isn't a neutral; denim counts as neutral, as it does in the lookbook rules."""
    return family(piece.get("colour") or "") not in NEUTRALS | {None} and not DENIM.search(piece.get("name", ""))


def check_outfits(outfits: list[dict], palette: Palette) -> tuple[list[str], list[str]]:
    """(pieces in an avoided colour family, outfits with more than one bright piece)."""
    off, loud = [], []
    for o in outfits:
        for p in o["pieces"]:
            if family(p.get("colour") or "") in palette.bad:
                off.append(f"{p['colour']} {p['name']}")
        if sum(bright(p) for p in o["pieces"]) > 1:
            loud.append(o["title"])
    return off, loud


def evaluate_photo(image: str, data: bytes, media_type: str, llm, build) -> PhotoResult:
    """`build(analysis_dict)` returns (lookbook, outfits the stylist reviewed)."""
    res = PhotoResult(image)
    t = time.perf_counter()
    try:
        person = llm.analyze_person([(data, media_type)])
    except Exception as e:  # one bad photo shouldn't stop the run
        res.error = f"analysis failed: {e}"
        return res
    res.analysis_ms = (time.perf_counter() - t) * 1000
    if not person.usable:
        res.error = "no face found"
        return res
    res.season, res.detail = person.colour_season, person.season_detail.lower()
    res.undertone, res.caveats = person.undertone, person.caveats
    analysis = person.model_dump()
    t = time.perf_counter()
    try:
        lb, reviewed = build(analysis)
    except Exception as e:
        res.error = f"lookbook failed: {e}"
        return res
    res.lookbook_ms = (time.perf_counter() - t) * 1000
    res.outfits = [o for s in lb["sections"] for o in s["outfits"]]
    res.reviewed = reviewed
    res.approved = sum(1 for o in res.outfits if o.get("reviewed"))
    res.off_palette, res.loud = check_outfits(res.outfits, Palette.from_analysis(analysis))
    return res


def summarise(results: list[PhotoResult], season: str = "", undertone: str = "") -> dict:
    ok = [r for r in results if not r.error]
    seasons, details, tones = Counter(r.season for r in ok), Counter(r.detail for r in ok), Counter(r.undertone for r in ok)
    outfits = [o for r in ok for o in r.outfits]
    pieces = sum(len(o["pieces"]) for o in outfits)
    reviewed = sum(r.reviewed for r in ok)
    latency = [r.analysis_ms + r.lookbook_ms for r in ok]
    top = lambda c: c.most_common(1)[0] if c else ("", 0)
    return {
        "photos": len(results), "errors": len(results) - len(ok), "ok": len(ok),
        "season": top(seasons), "detail": top(details), "undertone": top(tones),
        "seasons": dict(seasons), "details": dict(details),
        "season_accuracy": (seasons[season] / len(ok)) if season and ok else None,
        "undertone_accuracy": (tones[undertone] / len(ok)) if undertone and ok else None,
        "outfits": len(outfits), "pieces": pieces,
        "approval_rate": (sum(r.approved for r in ok) / reviewed) if reviewed else None, "reviewed": reviewed,
        "off_palette_rate": (sum(len(r.off_palette) for r in ok) / pieces) if pieces else None,
        "loud_rate": (sum(len(r.loud) for r in ok) / len(outfits)) if outfits else None,
        "empty": sum(1 for r in ok if not r.outfits),
        "latency_median_ms": sorted(latency)[len(latency) // 2] if latency else None,
        "latency_p95_ms": percentile(latency, 95),
    }


def to_markdown(results: list[PhotoResult], s: dict, model: str, season: str = "", undertone: str = "") -> str:
    share = lambda n: f"{n} of {s['ok']}"
    lines = [
        f"## Lookbook evaluation ({s['photos']} selfies of one person, model: {model})", "",
        "| Metric | Result | Out of |", "|---|---|---|",
        f"| Same colour season every time (most common: {s['season'][0] or 'n/a'}) | {_pct(s['season'][1] / s['ok'] if s['ok'] else None)} | {share(s['season'][1])} |",
        f"| Same sub-season (most common: {s['detail'][0] or 'n/a'}) | {_pct(s['detail'][1] / s['ok'] if s['ok'] else None)} | {share(s['detail'][1])} |",
        f"| Same undertone (most common: {s['undertone'][0] or 'n/a'}) | {_pct(s['undertone'][1] / s['ok'] if s['ok'] else None)} | {share(s['undertone'][1])} |",
    ]
    if season:
        lines.append(f"| Colour season matches the known answer ({season}) | {_pct(s['season_accuracy'])} | {s['ok']} |")
    if undertone:
        lines.append(f"| Undertone matches the known answer ({undertone}) | {_pct(s['undertone_accuracy'])} | {s['ok']} |")
    lines += [
        f"| Outfits the AI stylist approved | {_pct(s['approval_rate'])} | {s['reviewed']} reviewed |",
        f"| Pieces in a colour the analysis said to avoid | {_pct(s['off_palette_rate'])} | {s['pieces']} pieces |",
        f"| Outfits with more than one bright piece | {_pct(s['loud_rate'])} | {s['outfits']} outfits |",
        f"| Lookbooks with no outfit at all | {s['empty']} | {s['ok']} |",
        f"| Time per photo, median / p95 (analysis + lookbook) | {_ms(s['latency_median_ms'])} / {_ms(s['latency_p95_ms'])} | |",
        f"| Failed photos | {s['errors']} | {s['photos']} |",
        "", "Seasons read: " + (", ".join(f"{k} {v}" for k, v in sorted(s["details"].items(), key=lambda kv: -kv[1])) or "none"),
        "", "### Per photo (fill in the last column by eye)", "",
        "| Photo | Season | Undertone | Outfits | Problems | Lighting / filter notes | Would you wear them? |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.image} | error | | | {r.error} | | |")
            continue
        problems = [f"avoided colour: {', '.join(r.off_palette)}"] if r.off_palette else []
        problems += [f"two brights: {', '.join(r.loud)}"] if r.loud else []
        caveats = r.caveats.replace("|", "/").replace("\n", " ")
        lines.append(f"| {r.image} | {r.detail or r.season} | {r.undertone} | {r.approved}/{r.reviewed} approved "
                     f"| {'; '.join(problems) or '-'} | {caveats} | |")
    return "\n".join(lines) + "\n"


def run(photos_dir: Path, llm, build) -> list[PhotoResult]:
    files = sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in MEDIA_TYPES) if photos_dir.is_dir() else []
    return [evaluate_photo(p.name, p.read_bytes(), MEDIA_TYPES[p.suffix.lower()], llm, build) for p in files]


def main(argv: list[str] | None = None) -> None:
    root = Path.cwd()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--photos", type=Path, default=root / "evals" / "lookbook-photos")
    ap.add_argument("--out", type=Path, default=root / "evals" / "lookbook-results.md")
    ap.add_argument("--season", choices=["spring", "summer", "autumn", "winter"], help="your known colour season")
    ap.add_argument("--undertone", choices=["warm", "cool", "neutral"], help="your known undertone")
    ap.add_argument("--fake", action="store_true", help="use the offline FakeVision even if a key is set")
    args = ap.parse_args(argv)

    import fakeredis

    from .config import get_settings
    from .llm.client import FakeVision, make_vision_llm
    from .runtime import build_runtime
    from .services import stylist
    from .services.lookbook import build_lookbook
    from .services.ranking import UserContext
    from .services.trends import season_of

    settings = get_settings()
    rt = build_runtime(settings, import_catalog=False)
    if not rt.catalog.index:
        print(f"The catalog is empty: importing {settings.catalog_source} first.")
        rt = build_runtime(settings, import_catalog=True, background_import=False)
    llm = FakeVision() if args.fake or not settings.anthropic_api_key else make_vision_llm(
        settings.anthropic_api_key, settings.llm_model, settings.llm_timeout_s)
    cache = fakeredis.FakeRedis()  # fresh per run: every photo gets its own stylist call
    user = UserContext()

    def build(analysis: dict):
        seen = []

        def curate(request):
            seen.append(len(request.outfits))
            return stylist.curate(llm, cache, request, daily_limit=0)  # 0: no daily cap

        lb = build_lookbook(rt.catalog, user, SimpleNamespace(result=analysis), [], "seasons",
                            season=season_of(time.gmtime().tm_mon), stylist=curate)
        return lb, sum(seen)

    if not args.photos.is_dir() or not any(p.suffix.lower() in MEDIA_TYPES for p in args.photos.iterdir()):
        raise SystemExit(f"No photos in {args.photos}: put your selfies there (jpg, png or webp).")
    results = run(args.photos, llm, build)
    summary = summarise(results, args.season or "", args.undertone or "")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_markdown(results, summary, llm.name, args.season or "", args.undertone or ""), encoding="utf-8")
    print(args.out.read_text(encoding="utf-8"))
    print(f"Written to {args.out}")


if __name__ == "__main__":
    main()
