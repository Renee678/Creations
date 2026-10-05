"""Rule-based fit guidance per body shape.

Deliberately rules, not an LLM: the advice is deterministic, testable and
explainable, and the same keywords feed the ranking (fit bonus/penalty).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FitGuide:
    summary: str
    prefer: dict[str, list[str]] = field(default_factory=dict)  # category -> keywords to favour
    avoid: dict[str, list[str]] = field(default_factory=dict)   # category -> keywords to down-rank


GUIDES: dict[str, FitGuide] = {
    "pear": FitGuide(
        "Add detail at the shoulders and choose A-line or wide-leg bottoms to balance the hips.",
        prefer={"top": ["puff", "bow", "boat", "square", "cropped"], "bottom": ["wide", "a-line", "straight", "high-waisted"],
                "dress": ["a-line", "flared", "wrap"]},
        avoid={"bottom": ["skinny", "low-rise", "tight"], "dress": ["bodycon"]},
    ),
    "apple": FitGuide(
        "V-necks and fabrics with drape lengthen the torso; avoid anything tight at the waist.",
        prefer={"top": ["v-neck", "flowing", "relaxed", "wrap"], "dress": ["empire", "wrap", "a-line", "flowing"],
                "outerwear": ["long", "open"]},
        avoid={"top": ["cropped", "tight"], "dress": ["bodycon", "fitted"]},
    ),
    "hourglass": FitGuide(
        "Show off your waist: wrap, belted and fitted styles all work well.",
        prefer={"top": ["wrap", "fitted", "belted"], "dress": ["wrap", "belted", "fitted", "bodycon"],
                "bottom": ["high-waisted"]},
        avoid={"top": ["boxy", "oversized"], "dress": ["shift", "boxy"]},
    ),
    "rectangle": FitGuide(
        "Use belts, ruffles and layers to create curves.",
        prefer={"top": ["peplum", "ruffle", "belted", "wrap"], "dress": ["belted", "tiered", "ruffle"],
                "bottom": ["pleated", "flared"]},
        avoid={},
    ),
    "inverted_triangle": FitGuide(
        "Keep the top simple and add volume below with wide-leg trousers or A-line skirts.",
        prefer={"top": ["v-neck", "simple", "fitted"], "bottom": ["wide", "flared", "a-line", "pleated", "cargo"],
                "dress": ["a-line", "flared"]},
        avoid={"top": ["puff", "shoulder", "boat"]},
    ),
}
DEFAULT_GUIDE = FitGuide("Tell me your body shape and I'll give you more specific fit advice.")


def guide_for(body_shape: str) -> FitGuide:
    return GUIDES.get(body_shape, DEFAULT_GUIDE)


def fit_adjustment(body_shape: str, category: str, product_text: str) -> float:
    """Score nudge in [-1, 1] for how well a product's cut suits the body shape."""
    g = guide_for(body_shape)
    text = product_text.lower()
    good = sum(kw in text for kw in g.prefer.get(category, []))
    bad = sum(kw in text for kw in g.avoid.get(category, []))
    return max(-1.0, min(1.0, 0.5 * good - 0.7 * bad))


def bmi(height_cm: int, weight_kg: float) -> float:
    return round(weight_kg / (height_cm / 100) ** 2, 1)
