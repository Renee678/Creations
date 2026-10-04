"""Vision LLM clients.

ClaudeVision talks to the Anthropic API. FakeVision is a deterministic stand-in
used by tests, CI and anyone running the app without an API key, so the whole
pipeline (queue, worker, search, ranking) stays runnable on any machine.
"""

import base64
import hashlib
import logging
from typing import Protocol

from ..services.vocab import STYLES
from .schemas import DetectedItem, LookAnalysis

log = logging.getLogger(__name__)

ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class LLMError(Exception):
    """Base class; `retryable` tells the worker whether another attempt can help."""

    retryable = False


class LLMTransientError(LLMError):
    retryable = True


class LLMRefusedError(LLMError):
    pass


class VisionLLM(Protocol):
    name: str

    def analyze_look(self, image: bytes, media_type: str) -> LookAnalysis: ...


SYSTEM_PROMPT = f"""You are a fashion stylist who breaks outfit photos down into shoppable items.

For the image, list every clearly visible garment, pair of shoes, bag and accessory (at most 6),
most prominent first. Describe each one in plain English the way a product catalogue would:
category, colour, fit, distinguishing details, and a one-sentence search_query that a shopper
would type to find a similar product (include colour, garment type, fit and key details).

Use only these style labels for style_tags: {", ".join(STYLES)}.
Estimate the original retail price only when the item looks premium or designer; otherwise null.
If the image shows no clothing, set is_outfit to false and return no items.
Write vibe_zh in Simplified Chinese; everything else in English."""


class ClaudeVision:
    name = "claude"

    def __init__(self, api_key: str, model: str, timeout_s: float):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=2)
        self._model = model

    def analyze_look(self, image: bytes, media_type: str) -> LookAnalysis:
        a = self._anthropic
        try:
            response = self._client.beta.messages.parse(
                model=self._model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": media_type,
                            "data": base64.standard_b64encode(image).decode(),
                        }},
                        {"type": "text", "text": "Break this outfit down into shoppable items."},
                    ],
                }],
                output_format=LookAnalysis,
                # If the primary model declines, the API retries on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (a.RateLimitError, a.APIConnectionError, a.APITimeoutError, a.InternalServerError) as e:
            raise LLMTransientError(str(e)) from e
        except a.APIStatusError as e:
            if e.status_code >= 500:
                raise LLMTransientError(str(e)) from e
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e

        if response.stop_reason == "refusal":
            raise LLMRefusedError("the model declined to analyse this image")
        if response.parsed_output is None:
            raise LLMError(f"no structured output (stop_reason={response.stop_reason})")
        log.info("claude look analysis: %d items, usage=%s", len(response.parsed_output.items), response.usage)
        return response.parsed_output


# Canned looks for offline mode. Chosen by image hash so the same image always
# gets the same analysis, which keeps demos and tests reproducible.
_FAKE_LOOKS = [
    LookAnalysis(
        is_outfit=True, vibe_zh="法式老钱风：柔和米色针织配高腰阔腿裤，低调又有质感。",
        style_tags=["old_money", "quiet_luxury"],
        items=[
            DetectedItem(category="top", name="fine-knit V-neck cardigan", colour="beige", fit="relaxed",
                         details=["V-neck", "buttons", "ribbed cuffs"], style_tags=["old_money"],
                         search_query="beige fine-knit V-neck cardigan with buttons, relaxed fit",
                         estimated_original_price_usd=890),
            DetectedItem(category="bottom", name="wide-leg tailored trousers", colour="white", fit="wide-leg",
                         details=["high-waisted", "pressed pleats"], style_tags=["old_money", "office"],
                         search_query="white high-waisted wide-leg tailored trousers with pleats",
                         estimated_original_price_usd=650),
            DetectedItem(category="shoes", name="slingback kitten heels", colour="black", fit="pointed toe",
                         details=["slingback", "kitten heel"], style_tags=["quiet_luxury"],
                         search_query="black slingback kitten heel pumps with pointed toe",
                         estimated_original_price_usd=1100),
        ],
    ),
    LookAnalysis(
        is_outfit=True, vibe_zh="海边度假风：亚麻长裙配草编包和遮阳帽，轻松又上镜。",
        style_tags=["resort", "boho"],
        items=[
            DetectedItem(category="dress", name="linen maxi dress", colour="white", fit="flowing",
                         details=["sleeveless", "square neckline", "flared skirt"], style_tags=["resort"],
                         search_query="white sleeveless linen maxi dress with square neckline",
                         estimated_original_price_usd=420),
            DetectedItem(category="bag", name="woven straw cross-body bag", colour="beige", fit="small",
                         details=["woven straw", "adjustable strap"], style_tags=["boho", "resort"],
                         search_query="woven straw cross-body bag beige",
                         estimated_original_price_usd=380),
            DetectedItem(category="accessory", name="straw sun hat", colour="beige", fit="wide brim",
                         details=["ribbon band"], style_tags=["resort"],
                         search_query="wide-brimmed straw sun hat with ribbon",
                         estimated_original_price_usd=None),
        ],
    ),
    LookAnalysis(
        is_outfit=True, vibe_zh="千金甜美风：缎面蝴蝶结衬衫配百褶短裙和玛丽珍鞋。",
        style_tags=["coquette", "ballet_core"],
        items=[
            DetectedItem(category="top", name="satin bow blouse", colour="light pink", fit="flowing",
                         details=["tie bow neck", "puff sleeves", "satin"], style_tags=["coquette"],
                         search_query="light pink satin blouse with bow at the neck and puff sleeves",
                         estimated_original_price_usd=560),
            DetectedItem(category="bottom", name="pleated mini skirt", colour="white", fit="a-line",
                         details=["pleated", "high waist"], style_tags=["preppy", "coquette"],
                         search_query="white pleated mini skirt high waist",
                         estimated_original_price_usd=None),
            DetectedItem(category="shoes", name="Mary Jane ballet flats", colour="black", fit="flat",
                         details=["strap with buckle", "round toe"], style_tags=["ballet_core"],
                         search_query="black Mary Jane ballet flats with strap and buckle",
                         estimated_original_price_usd=750),
        ],
    ),
]


class FakeVision:
    name = "fake"

    def analyze_look(self, image: bytes, media_type: str) -> LookAnalysis:
        idx = int.from_bytes(hashlib.sha256(image).digest()[:4], "little") % len(_FAKE_LOOKS)
        return _FAKE_LOOKS[idx].model_copy(deep=True)


def make_vision_llm(api_key: str, model: str, timeout_s: float) -> VisionLLM:
    if api_key:
        return ClaudeVision(api_key, model, timeout_s)
    log.warning("ANTHROPIC_API_KEY not set: using the offline FakeVision model")
    return FakeVision()
