"""Style memory: every uploaded look and saved product becomes a StyleEvent.

The profile is plain aggregation (counts of colours, categories and style tags),
so it is cheap, testable and explainable. The LLM is not needed to remember.
"""

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm.schemas import LookAnalysis
from ..models import StyleEvent, User
from .ranking import UserContext
from .vocab import STYLES

DECLARED_WEIGHT = 0.3   # styles the user picked at sign-up
LEARNED_WEIGHT = 0.4    # max extra weight from observed behaviour


def record_look(session: Session, user_id: int, analysis: LookAnalysis) -> None:
    for item in analysis.items:
        session.add(StyleEvent(user_id=user_id, kind="uploaded", attributes={
            "category": item.category, "colour": item.colour.lower(), "fit": item.fit.lower(),
            "style_tags": [t for t in item.style_tags if t in STYLES],
        }))


def record_saved(session: Session, user_id: int, product, style_tags: list[str]) -> None:
    session.add(StyleEvent(user_id=user_id, kind="saved", attributes={
        "category": product.category, "colour": product.colour.lower(), "product_id": product.id,
        "style_tags": [t for t in style_tags if t in STYLES],
    }))


def style_counts(session: Session, user_id: int) -> dict[str, Counter]:
    events = session.scalars(select(StyleEvent).where(StyleEvent.user_id == user_id)).all()
    counts = {"style_tags": Counter(), "colour": Counter(), "category": Counter()}
    for e in events:
        weight = 2 if e.kind == "saved" else 1  # saving is a stronger signal than uploading
        for t in e.attributes.get("style_tags", []):
            counts["style_tags"][t] += weight
        counts["colour"][e.attributes.get("colour", "")] += weight
        counts["category"][e.attributes.get("category", "")] += weight
    for c in counts.values():
        c.pop("", None)
    return counts


def user_context(session: Session, user: User) -> UserContext:
    weights = {s: DECLARED_WEIGHT for s in user.preferred_styles}
    tags = style_counts(session, user.id)["style_tags"]
    total = sum(tags.values())
    for tag, n in tags.items():
        weights[tag] = round(weights.get(tag, 0) + LEARNED_WEIGHT * n / total, 3)
    return UserContext(body_shape=user.body_shape, budget_per_item=user.budget_per_item, style_weights=weights)
