import hashlib
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Look, SavedOutfit, TryOn
from ..services.colours import family
from ..services.body import guide_for
from ..services.lookbook import latest_analysis
from ..services.ranking import STYLE_KEYWORDS
from ..services.style_memory import record_saved, style_counts, user_context
from ..services.trends import SEASONS, season_of
from ..services.vocab import BODY_SHAPES, STYLES, style_name
from .profiles import get_user_or_404

router = APIRouter(prefix="/api/users/{user_id}", tags=["style"])


class SaveIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=32)
    style_tags: list[str] = Field(default_factory=list, max_length=12)


@router.post("/saved", status_code=201)
def save_product(user_id: int, body: SaveIn, request: Request, db: Session = Depends(get_db)) -> dict:
    get_user_or_404(db, user_id)
    product = request.app.state.runtime.catalog.products.get(body.product_id)
    if product is None:
        raise HTTPException(404, "product not found")
    record_saved(db, user_id, product, body.style_tags)
    db.commit()
    return {"saved": body.product_id}


@router.get("/style")
def style_profile(user_id: int, db: Session = Depends(get_db)) -> dict:
    user = get_user_or_404(db, user_id)
    counts = style_counts(db, user_id)
    ctx = user_context(db, user)
    top_styles = sorted(ctx.style_weights.items(), key=lambda kv: kv[1], reverse=True)[:5]
    analysis = latest_analysis(db, user_id)
    return {
        "analysis": analysis.result if analysis else None,  # the colour and face report from their photos
        "fit": fit_report(user.body_shape),
        "styles": [{"id": s, "label": STYLES.get(s, s), "weight": w} for s, w in top_styles],
        "colours": counts["colour"].most_common(5),
        "categories": counts["category"].most_common(5),
        "signals": sum(counts["category"].values()),
        "summary": summarise(top_styles, counts["colour"].most_common(3)),
    }


def summarise(top_styles, top_colours) -> str:
    """Template summary: deterministic and free. (An LLM paragraph is a possible upgrade.)"""
    if not top_styles:
        return "Not enough to go on yet. Upload a few outfits you like and I'll learn your style."
    names = " and ".join(style_name(s) for s, _ in top_styles[:2])
    text = f"You lean towards {names}"
    if top_colours:
        text += ", and you often wear " + ", ".join(c for c, _ in top_colours)
    return text + "."


def fit_report(body_shape: str) -> dict:
    """The same fit rules the ranking uses, as words to look for and to skip."""
    g = guide_for(body_shape)
    flat = lambda d: list(dict.fromkeys(w for words in d.values() for w in words))[:8]
    return {"shape": BODY_SHAPES.get(body_shape), "summary": g.summary, "look_for": flat(g.prefer), "skip": flat(g.avoid)}


# ---------- saved outfits ("My outfits" in My Style) ----------

Season = Literal["spring", "summer", "autumn", "winter"]


class OutfitIn(BaseModel):
    title: str = Field(default="", max_length=80)
    product_ids: list[str] = Field(min_length=2, max_length=8)
    season: Season | None = None
    style_id: str | None = None
    source: Literal["lookbook", "mine", "fitting_room"] = "lookbook"
    why: str | None = Field(default=None, max_length=300)        # the stylist's line, for the book page
    occasion: Literal["work", "weekend", "date", "party", "travel"] | None = None
    inspo_look_id: int | None = None                             # Make it mine: the look it started from


class OutfitRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)


def palette_dots(product, catalog, palette: dict[str, str]) -> list[dict]:
    """Other colours of this piece that are in the user's palette: display only, empty when none match."""
    own = family(product.colour) or family(product.name)
    dots, seen = [], {own}
    for v in catalog.colour_variants(product):
        fam = family(v.colour) or family(v.name)
        if fam in palette and fam not in seen:
            seen.add(fam)
            dots.append({"id": v.id, "colour": v.colour or fam, "hex": palette[fam]})
    return dots[:5]


def latest_tryon(db: Session, user_id: int, product_ids: list[str]) -> str | None:
    """The newest finished try-on of exactly this set of pieces: the outfit page's "On me" photo."""
    wanted = sorted(product_ids)
    rows = db.scalars(select(TryOn).where(TryOn.user_id == user_id, TryOn.status == "done")
                      .order_by(TryOn.created_at.desc()))
    rec = next((t for t in rows if sorted(t.product_ids) == wanted and t.result_image), None)
    return f"/api/tryons/{rec.id}/image" if rec else None


def outfit_out(o: SavedOutfit, catalog, db: Session | None = None, palette: dict[str, str] | None = None) -> dict:
    products = [catalog.products[i] for i in o.product_ids if i in catalog.products]
    pieces = [p.to_dict() | ({"palette_dots": palette_dots(p, catalog, palette)} if palette else {}) for p in products]
    inspo = db.get(Look, o.inspo_look_id) if db is not None and o.inspo_look_id else None
    inspo = inspo if inspo is not None and inspo.user_id == o.user_id else None  # only the user's own looks
    return {"id": o.id, "title": o.title, "season": o.season, "style_id": o.style_id,
            "style": STYLES.get(o.style_id, o.style_id), "source": o.source, "pieces": pieces,
            "total_price": round(sum(p["price"] for p in pieces), 2), "created_at": o.created_at.isoformat(),
            "why": o.why, "occasion": o.occasion,
            "inspo": {"id": inspo.id, "vibe": (inspo.result or {}).get("vibe", "")} if inspo else None,
            "tryon_image": latest_tryon(db, o.user_id, o.product_ids) if db is not None else None}


def _palette(db: Session, user_id: int) -> dict[str, str]:
    """Colour family -> the hex the user's analysis gave it, for the colours that suit them."""
    rec = latest_analysis(db, user_id)
    out: dict[str, str] = {}
    for c in ((rec.result or {}).get("best_colours", []) if rec else []):
        fam = family(c.get("name", ""))
        if fam and fam not in out:
            out[fam] = c.get("hex", "#cccccc")
    return out


def guess_style(products, fallback: str) -> str:
    """The style whose keywords the pieces mention most; deterministic, used when the caller doesn't say."""
    text = " ".join(f"{p.name} {p.product_type} {p.description}" for p in products).lower()
    hits = {s: sum(text.count(k) for k in words) for s, words in STYLE_KEYWORDS.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] else fallback


@router.post("/outfits")
def save_outfit(user_id: int, body: OutfitIn, request: Request, db: Session = Depends(get_db)):
    user = get_user_or_404(db, user_id)
    catalog = request.app.state.runtime.catalog
    ids = list(dict.fromkeys(body.product_ids))
    products = [catalog.products.get(i) for i in ids]
    if None in products:
        raise HTTPException(404, "product not found")
    key = hashlib.sha256(",".join(sorted(ids)).encode()).hexdigest()
    existing = db.scalar(select(SavedOutfit).where(SavedOutfit.user_id == user_id, SavedOutfit.pieces_key == key))
    if existing is not None:  # idempotent: saving the same set again (a double tap) keeps one copy
        return JSONResponse(outfit_out(existing, catalog), status_code=200)
    favourite = next(iter(user.preferred_styles or []), "minimalist")
    style = body.style_id if body.style_id in STYLES else guess_style(products, favourite)
    season = body.season or season_of(datetime.now(timezone.utc).month)
    title = body.title.strip() or f"{STYLES[style]} {season}"
    outfit = SavedOutfit(user_id=user_id, title=title, product_ids=ids, pieces_key=key, season=season,
                         style_id=style, source=body.source, why=(body.why or "").strip() or None,
                         occasion=body.occasion, inspo_look_id=body.inspo_look_id)
    db.add(outfit)
    for p in products:  # each piece also teaches the style memory, like a single save
        record_saved(db, user_id, p, [style])
    db.commit()
    return JSONResponse(outfit_out(outfit, catalog), status_code=201)


@router.get("/outfits")
def list_outfits(user_id: int, request: Request, season: Season | None = None,
                 style: str | None = Query(default=None, max_length=30), db: Session = Depends(get_db)) -> dict:
    get_user_or_404(db, user_id)
    catalog = request.app.state.runtime.catalog
    rows = list(db.scalars(select(SavedOutfit).where(SavedOutfit.user_id == user_id)
                           .order_by(SavedOutfit.created_at.desc(), SavedOutfit.id.desc())))
    shown = [o for o in rows if (season is None or o.season == season) and (style is None or o.style_id == style)]
    palette = _palette(db, user_id)
    return {
        "outfits": [outfit_out(o, catalog, db, palette) for o in shown],
        "seasons": [s for s in SEASONS if any(o.season == s for o in rows)],
        "styles": [{"id": s, "label": STYLES.get(s, s)} for s in dict.fromkeys(o.style_id for o in rows)],
        "total": len(rows),
        "this_season": sum(o.season == season_of(datetime.now(timezone.utc).month) for o in rows),
    }


@router.patch("/outfits/{outfit_id}")
def rename_outfit(user_id: int, outfit_id: int, body: OutfitRename, request: Request,
                  db: Session = Depends(get_db)) -> dict:
    outfit = db.get(SavedOutfit, outfit_id)
    if outfit is None or outfit.user_id != user_id:
        raise HTTPException(404, "outfit not found")
    outfit.title = body.title.strip()
    db.commit()
    return outfit_out(outfit, request.app.state.runtime.catalog, db)


@router.delete("/outfits/{outfit_id}", status_code=204)
def delete_outfit(user_id: int, outfit_id: int, db: Session = Depends(get_db)) -> None:
    outfit = db.get(SavedOutfit, outfit_id)
    if outfit is None or outfit.user_id != user_id:
        raise HTTPException(404, "outfit not found")
    db.delete(outfit)
    db.commit()
