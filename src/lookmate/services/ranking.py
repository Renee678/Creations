"""Re-rank vector-search candidates for one detected item.

score = 0.60 * visual/text similarity
      + 0.15 * style match   (item tags + the user's style memory)
      + 0.10 * body-shape fit
      + 0.15 * price score   (cheaper relative to the user's budget is better)

Similarity dominates so results still look like the inspiration; the other
terms only reorder close candidates. Each term also yields a human-readable
reason so the UI can explain every recommendation.
"""

import re
from dataclasses import dataclass, field

from ..catalog.service import SearchResult
from ..llm.schemas import DetectedItem
from .body import fit_adjustment
from .match import Target
from .vocab import BODY_SHAPES, style_name

W_SIM, W_STYLE, W_FIT, W_PRICE = 0.60, 0.15, 0.10, 0.15
W_COLOUR = 0.15  # on top: between two equally close pieces, the one in the original's colour wins

# Style label -> words that signal it in catalog descriptions.
STYLE_KEYWORDS = {
    "old_money": ["old money", "tailored", "cable", "pearl", "wool", "pleat"],
    "quiet_luxury": ["quiet luxury", "satin", "silk", "wool", "cashmere"],
    "minimalist": ["minimalist", "basic", "clean", "plain", "solid"],
    "clean_girl": ["clean girl", "rib", "fitted"],
    "coquette": ["coquette", "bow", "lace", "frill", "ruffle", "puff"],
    "ballet_core": ["ballet", "ballerina", "wrap", "mary jane"],
    "streetwear": ["streetwear", "oversized", "hoodie", "cargo", "graphic"],
    "y2k": ["y2k", "low-rise", "baby tee", "mini", "cropped"],
    "preppy": ["preppy", "pleated", "cable", "polo", "collar"],
    "boho": ["boho", "tiered", "straw", "crochet", "floral", "maxi"],
    "office": ["office", "blazer", "tailored", "shirt", "trousers"],
    "resort": ["resort", "beach", "linen", "straw", "sandals"],
}


@dataclass(frozen=True)
class UserContext:
    body_shape: str = "unsure"
    budget_per_item: float = 50.0
    style_weights: dict[str, float] = field(default_factory=dict)  # label -> 0..1 from profile + memory


@dataclass(frozen=True)
class RankedPick:
    product_id: str
    score: float
    reasons: list[str]


def style_score(tags: list[str], user: UserContext, text: str) -> tuple[float, list[str]]:
    text = text.lower()
    matched = [t for t in tags if any(k in text for k in STYLE_KEYWORDS.get(t, []))]
    personal = [t for t, w in user.style_weights.items() if w > 0 and any(k in text for k in STYLE_KEYWORDS.get(t, []))]
    score = min(1.0, 0.5 * len(matched) + sum(user.style_weights.get(t, 0) for t in personal))
    reasons = []
    if personal:
        reasons.append(f"Matches your {style_name(personal[0])} style")
    elif matched:
        reasons.append(f"Keeps the {style_name(matched[0])} feel of the original")
    return score, reasons


# Two registers that never stand in for each other: a tracksuit is no dupe for a tailored trouser.
REGISTERS = {
    "sporty": ("sport", "athletic", "gym", "track", "jogger", "hoodie", "sweatshirt", "legging", "running",
               "training", "active", "tracksuit", "fleece", "cycling short", "basketball", "football", "trainer"),
    "tailored": ("tailored", "blazer", "suit", "pleat", "satin", "silk", "wool", "structured", "formal", "smart",
                 "cigarette", "pencil", "court shoe", "pump", "loafer", "blouse", "trench"),
}


def register(text: str) -> str | None:
    """'sporty', 'tailored' or None (neither, or both) for a piece's description."""
    t = text.lower()
    hits = {r for r, words in REGISTERS.items()
            if any(re.search(rf"\b{re.escape(w)}(s|es|ed|y|ing)?\b", t) for w in words)}
    return hits.pop() if len(hits) == 1 else None


def same_register(item: DetectedItem, product_text: str) -> bool:
    """False only when the original and the candidate sit in opposite registers."""
    original = register(" ".join([item.name, item.fit, item.search_query, *item.details, *item.style_tags]))
    candidate = register(product_text)
    return not (original and candidate and original != candidate)


def price_score(price: float, budget: float) -> float:
    """1.0 for free, 0.5 at exactly the budget, 0 at twice the budget."""
    return max(0.0, 1.0 - price / (2 * budget))


def rank(item: DetectedItem, candidates: list[SearchResult], user: UserContext, k: int = 4) -> list[RankedPick]:
    target = Target(item.category, item.name, item.colour, item.details, item.fit)
    picks = []
    for c in candidates:
        p = c.product
        text = f"{p.name} {p.product_type} {p.description}"
        if not same_register(item, text):
            continue  # a sporty piece is never a dupe for a tailored one, or the other way round
        s_style, style_reasons = style_score(item.style_tags, user, text)
        s_fit = fit_adjustment(user.body_shape, p.category, text)
        s_price = price_score(p.price, user.budget_per_item)
        s_colour = target.colour_score(p)
        score = W_SIM * c.score + W_STYLE * s_style + W_FIT * s_fit + W_PRICE * s_price + W_COLOUR * s_colour

        reasons = []
        if s_colour == 1.0:  # same family and shade: "blue" is not the same colour as "dark blue"
            reasons.append("Same colour")
        elif s_colour == 0.0 and target.colour:
            reasons.append("Different colour")
        reasons += style_reasons
        if s_fit > 0:
            reasons.append(f"Cut suits your {BODY_SHAPES.get(user.body_shape, '').lower()} shape")
        if p.price <= user.budget_per_item:
            reasons.append("Within your budget")
        picks.append(RankedPick(p.id, round(score, 4), reasons))
    picks.sort(key=lambda r: r.score, reverse=True)
    return diversify(picks, candidates, k)


def diversify(picks: list[RankedPick], candidates: list[SearchResult], k: int) -> list[RankedPick]:
    """Prefer distinct designs: the same garment in four colours is one option, not four.

    Colour variants are only used to fill the list when there aren't enough distinct designs.
    """
    names = {c.product.id: c.product.name.lower() for c in candidates}
    chosen, seen, spare = [], set(), []
    for p in picks:
        if names[p.product_id] in seen:
            spare.append(p)
        else:
            seen.add(names[p.product_id])
            chosen.append(p)
    return (chosen + spare)[:k]
