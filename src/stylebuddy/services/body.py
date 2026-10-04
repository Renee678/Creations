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
        "肩部和上身可以多些细节，下装选 A 字或阔腿来平衡胯部。",
        prefer={"top": ["puff", "bow", "boat", "square", "cropped"], "bottom": ["wide", "a-line", "straight", "high-waisted"],
                "dress": ["a-line", "flared", "wrap"]},
        avoid={"bottom": ["skinny", "low-rise", "tight"], "dress": ["bodycon"]},
    ),
    "apple": FitGuide(
        "选择 V 领和有垂感的面料拉长上身，避免腰腹紧身。",
        prefer={"top": ["v-neck", "flowing", "relaxed", "wrap"], "dress": ["empire", "wrap", "a-line", "flowing"],
                "outerwear": ["long", "open"]},
        avoid={"top": ["cropped", "tight"], "dress": ["bodycon", "fitted"]},
    ),
    "hourglass": FitGuide(
        "突出腰线就对了：收腰、裹身、系带款都很合适。",
        prefer={"top": ["wrap", "fitted", "belted"], "dress": ["wrap", "belted", "fitted", "bodycon"],
                "bottom": ["high-waisted"]},
        avoid={"top": ["boxy", "oversized"], "dress": ["shift", "boxy"]},
    ),
    "rectangle": FitGuide(
        "可以用腰带、褶皱和层次感制造曲线。",
        prefer={"top": ["peplum", "ruffle", "belted", "wrap"], "dress": ["belted", "tiered", "ruffle"],
                "bottom": ["pleated", "flared"]},
        avoid={},
    ),
    "inverted_triangle": FitGuide(
        "上身保持简洁，用阔腿裤、A 字裙增加下半身的分量。",
        prefer={"top": ["v-neck", "simple", "fitted"], "bottom": ["wide", "flared", "a-line", "pleated", "cargo"],
                "dress": ["a-line", "flared"]},
        avoid={"top": ["puff", "shoulder", "boat"]},
    ),
}
DEFAULT_GUIDE = FitGuide("先告诉我你的身形，我会给出更具体的版型建议。")


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
