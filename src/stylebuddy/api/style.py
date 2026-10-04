from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.style_memory import record_saved, style_counts, user_context
from ..services.vocab import STYLES
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
    return {
        "styles": [{"id": s, "label": STYLES.get(s, s), "weight": w} for s, w in top_styles],
        "colours": counts["colour"].most_common(5),
        "categories": counts["category"].most_common(5),
        "signals": sum(counts["category"].values()),
        "summary_zh": summarise(top_styles, counts["colour"].most_common(3)),
    }


def summarise(top_styles, top_colours) -> str:
    """Template summary: deterministic and free. (An LLM paragraph is a possible upgrade.)"""
    if not top_styles:
        return "还没有足够的信息。上传几张喜欢的穿搭，我会慢慢了解你的风格。"
    names = "、".join(STYLES.get(s, s) for s, _ in top_styles[:2])
    text = f"你的穿搭偏向{names}"
    if top_colours:
        text += "，常出现的颜色是" + "、".join(c for c, _ in top_colours)
    return text + "。"
