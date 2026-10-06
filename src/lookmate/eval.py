"""Evaluate the AI on a labelled set of outfit photos: perception, dupes and (optionally) try-on.

    python scripts/run_eval.py                     # evals/cases.csv -> evals/results.md
    python -m lookmate.eval --tryon --person me.jpg  # also saves try-on images for a manual identity check

Each row of the cases file is one piece that should be found in one photo. Perception runs with Claude when
ANTHROPIC_API_KEY is set, else with the offline FakeVision (which only proves the harness works). The scores
are deterministic: they reuse the same rules (Target, colour families) the dupes search uses.
"""

import argparse
import csv
import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from .llm.schemas import DetectedItem, LookAnalysis
from .services.colours import family
from .services.match import Target, colour_family, length, same_colour, subtype

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
TOP_K = 5
YES, NO = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}


@dataclass
class Expected:
    """One piece that should be found in a photo. Blank attributes are not scored."""

    image: str
    category: str
    subtype: str = ""
    colour: str = ""
    length: str = ""
    pattern: bool | None = None   # True: printed/striped/checked; False: plain; None: not scored
    partial: bool | None = None   # True: cut off at the photo's edge
    notes: str = ""

    @property
    def colour_family(self) -> str | None:
        return family(self.colour) if self.colour else None

    def target(self) -> Target:
        """What a correct dupe must be, from the label rather than from the model's reading."""
        name = " ".join(x for x in (self.length, self.subtype or self.category) if x)
        return Target(self.category, name, self.colour, ["print"] if self.pattern else [])


def _flag(value: str) -> bool | None:
    v = (value or "").strip().lower()
    return True if v in YES else False if v in NO else None


def load_cases(path: Path) -> dict[str, list[Expected]]:
    """Image file -> the pieces expected in it, in file order."""
    cases: dict[str, list[Expected]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            image = (row.get("image") or "").strip()
            if not image or image.startswith("#"):
                continue
            cases[image].append(Expected(
                image=image, category=row["category"].strip().lower(),
                subtype=(row.get("subtype") or "").strip().lower(), colour=(row.get("colour") or "").strip().lower(),
                length=(row.get("length") or "").strip().lower(), pattern=_flag(row.get("pattern", "")),
                partial=_flag(row.get("partial", "")), notes=(row.get("notes") or "").strip()))
    return dict(cases)


def _agreement(exp: Expected, item: DetectedItem) -> int:
    t = Target(item.category, item.name, item.colour, item.details, item.fit)
    return sum([bool(exp.subtype) and t.subtype == exp.subtype,
                bool(exp.colour_family) and t.colour is not None and same_colour(exp.colour_family, t.colour)])


def match_pieces(expected: list[Expected], items: list[DetectedItem]) -> list[tuple[Expected, int | None]]:
    """Pair each expected piece with the detected item of the same category that agrees with it most.

    Each detected item is used once; an expected piece with no same-category item is a miss (None).
    """
    free = set(range(len(items)))
    pairs = []
    for exp in expected:
        options = [i for i in free if items[i].category == exp.category]
        best = max(options, key=lambda i: (_agreement(exp, items[i]), -i), default=None)
        if best is not None:
            free.discard(best)
        pairs.append((exp, best))
    return pairs


def score_attributes(exp: Expected, item: DetectedItem) -> dict[str, bool]:
    """Per attribute the label gives: did the model read it right?"""
    t = Target(item.category, item.name, item.colour, item.details, item.fit)
    text = " ".join([item.name, item.fit, *item.details])
    out = {"category": True}
    if exp.subtype:
        out["subtype"] = (subtype(item.category, item.name) or subtype(item.category, text)) == exp.subtype
    if exp.colour_family:
        got = colour_family(item.colour, item.name)
        out["colour"] = got is not None and same_colour(exp.colour_family, got)
    if exp.length:
        out["length"] = length(text) == exp.length
    if exp.pattern is not None:
        out["pattern"] = t.patterned == exp.pattern
    if exp.partial is not None:
        out["cut_off"] = item.partial == exp.partial
    return out


def dupes_precision(exp: Expected, picks: list) -> tuple[int, int]:
    """(top-k picks that are right for the labelled piece, picks shown)."""
    shown = picks[:TOP_K]
    target = exp.target()
    return sum(target.check(p) for p in shown), len(shown)


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(q / 100 * len(ordered))))
    return ordered[rank - 1]


@dataclass
class CaseResult:
    image: str
    expected: list[Expected]
    detected: list[DetectedItem] = field(default_factory=list)
    pairs: list[tuple[Expected, int | None]] = field(default_factory=list)
    attributes: list[dict[str, bool]] = field(default_factory=list)  # one per matched pair
    precision: list[tuple[int, int]] = field(default_factory=list)   # one per matched pair
    perception_ms: float = 0.0
    dupes_ms: float = 0.0
    error: str | None = None
    tryon_image: str | None = None


def evaluate_case(image: str, expected: list[Expected], data: bytes, media_type: str, llm, dupes) -> CaseResult:
    """Run perception and the dupes search on one photo and score it against its labels.

    `dupes(analysis)` returns the dupes result (sections with picks), as the app's find_dupes does.
    """
    res = CaseResult(image, expected)
    t0 = time.perf_counter()
    try:
        analysis: LookAnalysis = llm.analyze_look(data, media_type)
    except Exception as e:  # a failed call is a result too
        res.error = f"perception failed: {e}"
        return res
    res.perception_ms = (time.perf_counter() - t0) * 1000
    res.detected = list(analysis.items)
    res.pairs = match_pieces(expected, res.detected)
    t0 = time.perf_counter()
    result = dupes(analysis)
    res.dupes_ms = (time.perf_counter() - t0) * 1000
    sections = result["sections"]
    for exp, i in res.pairs:
        if i is None:
            continue
        res.attributes.append(score_attributes(exp, res.detected[i]))
        # find_dupes puts cut-off pieces last, so its sections are found by their item, not by position.
        section = next((s for s in sections if s["item"] == res.detected[i].model_dump()), {"picks": []})
        res.precision.append(dupes_precision(exp, [SimpleNamespace(**p) for p in section["picks"]]))
    return res


def summarise(results: list[CaseResult]) -> dict:
    expected = sum(len(r.expected) for r in results)
    matched = sum(1 for r in results for _, i in r.pairs if i is not None)
    acc: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        for a in r.attributes:
            for k, v in a.items():
                if k != "category":
                    acc[k].append(v)
    right = sum(p for r in results for p, _ in r.precision)
    shown = sum(n for r in results for _, n in r.precision)
    sections = [n for r in results for _, n in r.precision]
    extra = sum(len(r.detected) - sum(1 for _, i in r.pairs if i is not None) for r in results if not r.error)
    latency = [r.perception_ms + r.dupes_ms for r in results if not r.error]
    return {
        "images": len(results), "errors": sum(1 for r in results if r.error), "expected": expected,
        "category_recall": (matched / expected) if expected else None, "matched": matched,
        "accuracy": {k: (sum(v) / len(v), len(v)) for k, v in acc.items()},
        "dupes_precision": (right / shown) if shown else None, "dupes_shown": shown,
        "zero_result_rate": (sum(1 for n in sections if n == 0) / len(sections)) if sections else None,
        "sections": len(sections), "extra_items": extra,
        "latency_median_ms": statistics.median(latency) if latency else None,
        "latency_p95_ms": percentile(latency, 95),
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _ms(x: float | None) -> str:
    return "n/a" if x is None else f"{x / 1000:.1f} s" if x >= 1000 else f"{x:.0f} ms"


def to_markdown(results: list[CaseResult], summary: dict, model: str) -> str:
    s = summary
    lines = [
        f"## Image evaluation ({s['images']} photos, {s['expected']} labelled pieces, perception: {model})", "",
        "| Metric | Result | Out of |", "|---|---|---|",
        f"| Category recall (labelled pieces found) | {_pct(s['category_recall'])} | {s['expected']} |",
    ]
    names = {"subtype": "Garment type (e.g. skirt vs trousers)", "colour": "Colour family", "length": "Length",
             "pattern": "Pattern vs plain", "cut_off": "\"Cut off\" flag"}
    for key in ("subtype", "colour", "length", "pattern", "cut_off"):
        if key in s["accuracy"]:
            value, n = s["accuracy"][key]
            lines.append(f"| {names[key]} accuracy | {_pct(value)} | {n} |")
    lines += [
        f"| Dupes precision@{TOP_K} (right type, colour, length, pattern) | {_pct(s['dupes_precision'])} | {s['dupes_shown']} picks |",
        f"| Pieces with no dupes shown | {_pct(s['zero_result_rate'])} | {s['sections']} |",
        f"| Extra items detected (not labelled) | {s['extra_items']} | |",
        f"| Latency per photo, median / p95 (perception + search) | {_ms(s['latency_median_ms'])} / {_ms(s['latency_p95_ms'])} | |",
        f"| Failed calls | {s['errors']} | {s['images']} |",
        "", "### Per photo", "",
        "| Photo | Labelled | Found | Wrong attributes | Dupes right | Latency |", "|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.image} | {len(r.expected)} | error | {r.error} | | |")
            continue
        found = sum(1 for _, i in r.pairs if i is not None)
        wrong = []
        for (exp, i), attrs in zip([p for p in r.pairs if p[1] is not None], r.attributes):
            bad = [k for k, ok in attrs.items() if not ok]
            if bad:
                wrong.append(f"{exp.subtype or exp.category}: {', '.join(bad)}")
        missed = [e.subtype or e.category for e, i in r.pairs if i is None]
        if missed:
            wrong.append("missed " + ", ".join(missed))
        right, shown = sum(p for p, _ in r.precision), sum(n for _, n in r.precision)
        lines.append(f"| {r.image} | {len(r.expected)} | {found} | {'; '.join(wrong) or '-'} | {right}/{shown} "
                     f"| {_ms(r.perception_ms + r.dupes_ms)} |")
    tried = [r for r in results if r.tryon_image]
    if tried:
        lines += ["", "### Try-on identity (check by eye, fill in pass/fail)", "",
                  "| Photo | Try-on image | Same face and hair? |", "|---|---|---|"]
        lines += [f"| {r.image} | {r.tryon_image} | |" for r in tried]
    return "\n".join(lines) + "\n"


def run(cases_path: Path, photos_dir: Path, llm, dupes, tryon=None) -> tuple[list[CaseResult], dict]:
    """Evaluate every case; `tryon(result)` (optional) renders and returns the saved image path."""
    results = []
    for image, expected in load_cases(cases_path).items():
        path = photos_dir / image
        if not path.is_file():
            results.append(CaseResult(image, expected, error=f"photo not found: {path}"))
            continue
        res = evaluate_case(image, expected, path.read_bytes(), MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg"),
                            llm, dupes)
        if tryon is not None and not res.error:
            res.tryon_image = tryon(res)
        results.append(res)
    return results, summarise(results)


def main(argv: list[str] | None = None) -> None:
    root = Path.cwd()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", type=Path, default=root / "evals" / "cases.csv")
    ap.add_argument("--photos", type=Path, default=root / "evals" / "photos")
    ap.add_argument("--out", type=Path, default=root / "evals" / "results.md")
    ap.add_argument("--fake", action="store_true", help="use the offline FakeVision even if a key is set")
    ap.add_argument("--tryon", action="store_true", help="also dress --person in each photo's top dupes")
    ap.add_argument("--person", type=Path, help="full-body photo (or My model image) for --tryon")
    args = ap.parse_args(argv)

    from .config import get_settings
    from .llm.client import FakeVision, make_vision_llm
    from .runtime import build_runtime
    from .services.dupes import find_dupes
    from .services.ranking import UserContext

    settings = get_settings()
    rt = build_runtime(settings, import_catalog=False)  # the app's catalog as it is (on the server: the real one)
    if not rt.catalog.index:
        print(f"The catalog is empty: importing {settings.catalog_source} first.")
        rt = build_runtime(settings, import_catalog=True, background_import=False)
    llm = FakeVision() if args.fake or not settings.anthropic_api_key else make_vision_llm(
        settings.anthropic_api_key, settings.llm_model, settings.llm_timeout_s)
    user = UserContext()

    def dupes(analysis):
        return find_dupes(analysis, rt.catalog, user)

    tryon = None
    if args.tryon:
        if not args.person or not args.person.is_file():
            raise SystemExit("--tryon needs --person path/to/full-body.jpg")
        tryon = _tryon_runner(rt, args.person, args.out.parent / "out")
    results, summary = run(args.cases, args.photos, llm, dupes, tryon)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_markdown(results, summary, llm.name), encoding="utf-8")
    print(args.out.read_text(encoding="utf-8"))
    print(f"Written to {args.out}")


def _tryon_runner(rt, person: Path, out_dir: Path):
    """Dress the person in each photo's top dupe per piece, the way the fitting room does, and save it."""
    from .tryon.face import face_crop, paste_head
    from .tryon.garments import garment_for

    photo = person.read_bytes()
    media_type = MEDIA_TYPES.get(person.suffix.lower(), "image/jpeg")
    out_dir.mkdir(parents=True, exist_ok=True)

    def tryon(res: CaseResult) -> str | None:
        if not rt.tryon.renders or not getattr(rt.tryon, "whole_outfit", False):
            return None  # needs Nano Banana (REPLICATE_API_TOKEN)
        from .services.dupes import find_dupes
        from .services.ranking import UserContext

        analysis = LookAnalysis(is_outfit=True, vibe="", style_tags=[], items=res.detected)
        sections = find_dupes(analysis, rt.catalog, UserContext())["sections"]
        pieces = [rt.catalog.products[s["picks"][0]["id"]] for s in sections if s["picks"] and not s["hidden"]]
        garments = [garment_for(p, rt.data_dir) for p in pieces]
        face = face_crop(photo)
        image, _ = rt.tryon.dress_outfit(photo, media_type, garments, face) if face and getattr(
            rt.tryon, "takes_face", False) else rt.tryon.dress_outfit(photo, media_type, garments)
        image = (paste_head(photo, image) or (image, ""))[0]
        path = out_dir / f"{Path(res.image).stem}-tryon.jpg"
        path.write_bytes(image)
        return str(path)

    return tryon


if __name__ == "__main__":
    main()
