"""Why a piece gets no dupes: the closest catalog products and the first rule each one breaks.

    python -m lookmate.why_no_dupes --piece jeans                    # the latest analysed photo with jeans in it
    python -m lookmate.why_no_dupes --look 42 --piece jeans          # the piece as the AI read it in look 42
    python -m lookmate.why_no_dupes "light blue straight-leg jeans" --colour "light blue" --category bottom

Read-only. On the server: docker compose exec api python -m lookmate.why_no_dupes --piece jeans
"""

import argparse
from collections import Counter

from .llm.schemas import DetectedItem
from .services.dupes import CANDIDATES_PER_ITEM
from .services.match import Target


def explain(catalog, item: DetectedItem, max_price: float | None = None, show: int = 15) -> list[str]:
    """Report lines: how many of the closest products each rule rejects, then the closest ones and why."""
    target = Target.of(item)
    query = f"{item.colour} {item.name}. {item.search_query}. {' '.join(item.details)}"
    pool = [r.product for r in catalog.search(query, k=CANDIDATES_PER_ITEM, category=item.category)]
    reasons = [(p, target.rejection(p)) for p in pool]
    passed = [p for p, why in reasons if why is None]
    lines = [f"Piece: {item.colour} {item.name} ({item.category}); details: {', '.join(item.details) or '-'}",
             f"Read as: type={target.subtype} colour={target.colour} shade={target.shade} leg={target.leg} "
             f"cropped={target.cropped} sleeve={target.sleeve} warmth={target.warmth} patterned={target.patterned}",
             f"Closest {len(pool)} products in {item.category}: {len(passed)} pass every rule"
             + (f", {sum(p.price <= max_price for p in passed)} of them at ${max_price:g} or less" if max_price else ""),
             "Rejected by: " + (", ".join(f"{why} {n}" for why, n in Counter(w for _, w in reasons if w).most_common())
                                or "nothing")]
    lines.append(f"The closest {min(show, len(pool))}:")
    for p, why in reasons[:show]:
        lines.append(f"  {'PASS' if why is None else why:<11} ${p.price:<7g} {p.colour or '-':<16} {p.name}")
    return lines


def _item_from_look(look_id: int | None, piece: str | None) -> DetectedItem:
    """The piece as the AI read it: from that look, or from the latest analysed look that has it."""
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Look

    with SessionLocal() as db:
        if look_id is not None:
            look = db.get(Look, look_id)
            looks = [look] if look is not None and look.result else []
        else:
            looks = db.scalars(select(Look).where(Look.status == "done").order_by(Look.id.desc()).limit(200)).all()
        for look in looks:
            items = [s["item"] for s in (look.result or {}).get("sections", [])]
            chosen = [i for i in items if not piece or piece.lower() in i["name"].lower()]
            if chosen:
                print(f"From look {look.id}")
                return DetectedItem(**chosen[0])
    raise SystemExit(f"no analysed look with a piece matching {piece!r}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?", help="the piece, e.g. 'light blue straight-leg jeans' (or use --look)")
    ap.add_argument("--look", type=int, help="take the piece from this analysed look (its id in the URL or DB)")
    ap.add_argument("--piece", help="with --look: a word from the piece's name, e.g. jeans")
    ap.add_argument("--colour", default="")
    ap.add_argument("--category", default="bottom")
    ap.add_argument("--details", default="", help="comma-separated, e.g. 'high-rise,straight leg'")
    ap.add_argument("--max-price", type=float, default=None)
    args = ap.parse_args(argv)
    if args.look is not None or args.piece:
        item = _item_from_look(args.look, args.piece)
    elif args.name:
        item = DetectedItem(category=args.category, name=args.name, colour=args.colour, fit="",
                            details=[d.strip() for d in args.details.split(",") if d.strip()], style_tags=[],
                            search_query=f"{args.colour} {args.name}".strip())
    else:
        raise SystemExit("give a piece name, --piece WORD or --look ID")

    from .config import get_settings
    from .runtime import build_runtime

    rt = build_runtime(get_settings(), import_catalog=False)
    print("\n".join(explain(rt.catalog, item, args.max_price)))


if __name__ == "__main__":
    main()
