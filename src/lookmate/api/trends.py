from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.trends import latest_batch

router = APIRouter(prefix="/api/trends", tags=["trends"])


@router.get("")
def list_trends(request: Request, db: Session = Depends(get_db)) -> dict:
    catalog = request.app.state.runtime.catalog
    batch = latest_batch(db)
    return {
        "refreshed_at": batch[0].refreshed_at.isoformat() if batch else None,
        "origin": batch[0].origin if batch else None,
        "trends": [
            {
                "style_id": t.style_id, "label_zh": t.label_zh, "description_zh": t.description_zh,
                "keywords": t.keywords, "sources": t.sources,
                "examples": [r.product.__dict__ for r in catalog.search(t.example_query, k=4)],
            }
            for t in batch
        ],
    }
