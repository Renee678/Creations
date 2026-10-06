from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.lookbook import Palette, latest_analysis, outfit_styles
from ..services.style_memory import user_context
from ..services.trends import KINDS, SEASONS, season_of, trend_fit, trends_for_every_season
from ..services.vocab import STYLES
from .profiles import get_user_or_404

router = APIRouter(prefix="/api/trends", tags=["trends"])

SHOPPABLE = {"pieces", "bags_shoes", "colour"}  # kinds with product examples (not makeup)


@router.get("")
def list_trends(request: Request, user_id: int | None = Query(default=None), db: Session = Depends(get_db)) -> dict:
    """Trends by season and kind. With a user, each one also says whether it suits them."""
    rt = request.app.state.runtime
    catalog = rt.catalog
    # Never an empty page or season: seasons research hasn't covered (or nothing stored yet) come from the seed.
    batch = trends_for_every_season(db, rt.data_dir)
    palette, styles = Palette.from_analysis(None), []
    if user_id is not None:
        user = user_context(db, get_user_or_404(db, user_id))
        rec = latest_analysis(db, user_id)
        analysis = rec.result if rec else None
        palette = Palette.from_analysis(analysis)
        styles = outfit_styles(user, analysis, [])[:2]
    seasons = [s for s in SEASONS if any(t.season == s for t in batch)]
    current = season_of(datetime.now(timezone.utc).month)
    return {
        "refreshed_at": batch[0].refreshed_at.isoformat() if batch else None,
        "origin": batch[0].origin if batch else None,
        "seasons": seasons,
        "year": datetime.now(timezone.utc).year,
        "current_season": current if current in seasons else (seasons[0] if seasons else current),
        "kinds": KINDS,
        "my_styles": [{"id": s, "label": STYLES[s]} for s in styles],
        "trends": [
            {
                "season": t.season, "kind": t.kind, "style_id": t.style_id, "style": STYLES.get(t.style_id, t.style_id),
                "label": t.label, "description": t.description, "keywords": t.keywords, "sources": t.sources,
                "colours": t.colours or [],
                "fit": trend_fit(t, palette, styles),
                "examples": [r.product.to_dict() for r in catalog.search(t.example_query, k=4)]
                if t.kind in SHOPPABLE and t.example_query else [],
            }
            for t in batch
        ],
    }
