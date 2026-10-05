from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.body import guide_for
from ..services.lookbook import latest_analysis
from ..services.style_memory import record_saved, style_counts, user_context
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
