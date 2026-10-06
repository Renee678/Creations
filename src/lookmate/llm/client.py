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
from .schemas import (DetectedItem, LookAnalysis, PersonAnalysis, PhotoCheck, StyledOutfit, StylingRequest,
                      StylingResult, Swatch)

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

    def analyze_person(self, photos: list[tuple[bytes, str]]) -> PersonAnalysis: ...

    def check_photo(self, image: bytes, media_type: str) -> PhotoCheck: ...

    def curate_outfits(self, request: StylingRequest) -> StylingResult: ...


SYSTEM_PROMPT = f"""You are a fashion stylist who breaks outfit photos down into shoppable items.

For the image, list the main pieces of the outfit: each garment, pair of shoes, bag and accessory
that is mostly in frame (about half or more visible), at most 6, most prominent first. A piece that
is only a sliver at the edge of the photo is not what the user is asking about: list it only if it is
still clearly identifiable, last, with partial set to true. Describe each one in plain English the way a product catalogue would:
category, colour, fit, distinguishing details, and a one-sentence search_query that a shopper
would type to find a similar product (include colour, garment type, fit and key details).
For each garment also judge from the photo its sleeve length (sleeveless, short or long; cap sleeves are short)
and its warmth: summer (thin jersey, linen, cap sleeves, straps), winter (chunky, cable or roll-neck knit,
wool, fleece, padding) or all-season. A thin fitted top is not a jumper: name it for what it is.

Use only these style labels for style_tags: {", ".join(STYLES)}.
Estimate the original retail price only when the item looks premium or designer; otherwise null.
If the image shows no clothing, set is_outfit to false and return no items.
Write everything in English."""

PERSON_PROMPT = f"""You are a professional personal stylist and colour analyst. The user has shared 1-3 photos
of themselves and asked for personal styling advice.

- Seasonal colour analysis: judge undertone, value and contrast from skin, hair and eye colour, then
  pick one of spring, summer, autumn or winter plus a sub-season. Recommend 8 clothing colours that
  flatter them and 4 to keep away from the face, each with an approximate hex code.
- Face shape, the current hairstyle, 3 hairstyle or hair colour ideas, and 4 makeup suggestions
  (base, cheeks, eyes, lips) in tones that suit their season.
- Only if a full-body photo is included: neutral advice on proportions and cuts. Never comment on
  weight or attractiveness.
- Do not guess ethnicity, age or any other sensitive trait. Lighting, white balance and filters change
  how colour reads, so mention them in caveats when they matter.
- Use only these style labels for style_tags: {", ".join(STYLES)}.
- photo_checks: one entry per photo, in the order given. Say how the person is framed, whether the photo
  works for colour analysis, and whether it works for a virtual try-on (one person, standing, facing the
  camera, head to at least the knees). Every photo of the person helps read their style, so the tip says,
  warmly, what this photo adds, with at most one soft hint (e.g. daylight shows colours truest). Never call
  a photo of the person unusable or list what is wrong with it.
If no person's face is clearly visible, set usable to false and fill the rest with your best neutral defaults.
Write everything in English."""


PHOTO_CHECK_PROMPT = """You check one photo before a virtual try-on. Say how the person is framed, whether
the face reads well for colour analysis, and whether it works for a try-on: one person, standing, facing
the camera, visible from head to at least the knees, not cropped. The tip tells the user, kindly and in
one short sentence, what the photo is good for or how to retake it. Never comment on weight or looks.
Write in English."""


STYLIST_PROMPT = """You are a fashion stylist with a sharp, editorial eye, dressing one client from a shop's stock.

For each outfit you get the style it must express (with its definition), the season or occasion, the client's
colour palette and colours to avoid, and for every slot up to 5 candidates that already fit the client's price
range, body shape and palette. Pick exactly one candidate per slot so the outfit looks cohesive and intentional:
- colours that work together: at most one accent; "neutral" slots stay calm and grounding;
- fabrics, weight and formality that belong together and suit the season or occasion;
- faithful to the style definition (never something loud for a muted style).
When candidates are equally good, prefer the one listed first.
You are also the quality gate: nothing reaches the client unless you approve it. Approve an outfit only if you
would proudly show it in a lookbook. If no combination of its candidates works (clashing colours, a piece that
breaks the style, mismatched formality), set approved to false; it will not be shown. Don't reject over small
matters of taste.
Return the chosen ids in slot order and, for approved outfits, one short, casual sentence of 12 words or fewer,
said to the client like a friend would (e.g. "Soft camel and cream, easy and polished for a Monday."); for
rejected ones, what clashes. Write in English."""


class ClaudeVision:
    name = "claude"

    def __init__(self, api_key: str, model: str, timeout_s: float):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=2)
        self._model = model

    def analyze_look(self, image: bytes, media_type: str) -> LookAnalysis:
        result = self._parse(SYSTEM_PROMPT, [_image_block(image, media_type)],
                             "Break this outfit down into shoppable items.", LookAnalysis)
        log.info("claude look analysis: %d items", len(result.items))
        return result

    def analyze_person(self, photos: list[tuple[bytes, str]]) -> PersonAnalysis:
        blocks = [_image_block(data, media_type) for data, media_type in photos]
        result = self._parse(PERSON_PROMPT, blocks, "Here are my photos. What suits me?", PersonAnalysis)
        log.info("claude person analysis: season=%s usable=%s", result.colour_season, result.usable)
        return result

    def check_photo(self, image: bytes, media_type: str) -> PhotoCheck:
        return self._parse(PHOTO_CHECK_PROMPT, [_image_block(image, media_type)], "Is this photo good for try-on?",
                           PhotoCheck)

    def curate_outfits(self, request: StylingRequest) -> StylingResult:
        return self._parse(STYLIST_PROMPT, [], "Curate these outfits:\n" + request.model_dump_json(indent=1),
                           StylingResult)

    def _parse(self, system: str, images: list[dict], text: str, output_format):
        a = self._anthropic
        try:
            response = self._client.beta.messages.parse(
                model=self._model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": [*images, {"type": "text", "text": text}]}],
                output_format=output_format,
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
        log.info("claude usage=%s", response.usage)
        return response.parsed_output


def _image_block(data: bytes, media_type: str) -> dict:
    return {"type": "image", "source": {
        "type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode(),
    }}


# Canned looks for offline mode. Chosen by image hash so the same image always
# gets the same analysis, which keeps demos and tests reproducible.
_FAKE_LOOKS = [
    LookAnalysis(
        is_outfit=True, vibe="French old money: a soft beige knit with high-waisted wide-leg trousers, understated and polished.",
        style_tags=["old_money", "quiet_luxury"],
        items=[
            DetectedItem(category="top", name="fine-knit V-neck cardigan", colour="beige", fit="relaxed",
                         details=["V-neck", "buttons", "ribbed cuffs"], style_tags=["old_money"],
                         search_query="beige fine-knit V-neck cardigan with buttons, relaxed fit",
                         sleeve="long", warmth="all-season",
                         estimated_original_price_usd=890),
            DetectedItem(category="bottom", name="wide-leg tailored trousers", colour="white", fit="wide-leg",
                         details=["high-waisted", "pressed pleats"], style_tags=["old_money", "office"],
                         search_query="white high-waisted wide-leg tailored trousers with pleats", warmth="all-season",
                         estimated_original_price_usd=650),
            DetectedItem(category="shoes", name="slingback kitten heels", colour="black", fit="pointed toe",
                         details=["slingback", "kitten heel"], style_tags=["quiet_luxury"],
                         search_query="black slingback kitten heel pumps with pointed toe", warmth="all-season",
                         estimated_original_price_usd=1100),
        ],
    ),
    LookAnalysis(
        is_outfit=True, vibe="Seaside resort: a linen maxi dress with a straw bag and sun hat, easy and photogenic.",
        style_tags=["resort", "boho"],
        items=[
            DetectedItem(category="dress", name="linen maxi dress", colour="white", fit="flowing",
                         details=["sleeveless", "square neckline", "flared skirt"], style_tags=["resort"],
                         search_query="white sleeveless linen maxi dress with square neckline",
                         sleeve="sleeveless", warmth="summer",
                         estimated_original_price_usd=420),
            DetectedItem(category="bag", name="woven straw cross-body bag", colour="beige", fit="small",
                         details=["woven straw", "adjustable strap"], style_tags=["boho", "resort"],
                         search_query="woven straw cross-body bag beige", warmth="summer",
                         estimated_original_price_usd=380),
            DetectedItem(category="accessory", name="straw sun hat", colour="beige", fit="wide brim",
                         details=["ribbon band"], style_tags=["resort"],
                         search_query="wide-brimmed straw sun hat with ribbon", warmth="summer",
                         estimated_original_price_usd=None),
        ],
    ),
    LookAnalysis(
        is_outfit=True, vibe="Sweet coquette: a satin bow blouse with a pleated mini skirt and Mary Janes.",
        style_tags=["coquette", "ballet_core"],
        items=[
            DetectedItem(category="top", name="satin bow blouse", colour="light pink", fit="flowing",
                         details=["tie bow neck", "puff sleeves", "satin"], style_tags=["coquette"],
                         search_query="light pink satin blouse with bow at the neck and puff sleeves",
                         sleeve="short", warmth="all-season",
                         estimated_original_price_usd=560),
            DetectedItem(category="bottom", name="pleated mini skirt", colour="white", fit="a-line",
                         details=["pleated", "high waist"], style_tags=["preppy", "coquette"],
                         search_query="white pleated mini skirt high waist", warmth="all-season",
                         estimated_original_price_usd=None),
            DetectedItem(category="shoes", name="Mary Jane ballet flats", colour="black", fit="flat",
                         details=["strap with buckle", "round toe"], style_tags=["ballet_core"],
                         search_query="black Mary Jane ballet flats with strap and buckle", warmth="all-season",
                         estimated_original_price_usd=750),
        ],
    ),
]


def _swatches(spec: str) -> list[Swatch]:
    return [Swatch(name=n, hex=h) for n, h in (pair.split("=") for pair in spec.split(", "))]


# Canned personal analyses for offline mode, one per colour season.
_FAKE_PEOPLE = [
    PersonAnalysis(
        usable=True, colour_season="summer", season_detail="soft summer", undertone="cool", contrast="low",
        colouring_notes="Ash-brown hair, grey-green eyes and cool pink undertones with soft overall contrast.",
        best_colours=_swatches("dusty rose=#c48b9f, powder blue=#a7c4dc, lavender=#b9a7d1, sage=#a3b59a, "
                               "navy=#2f3e5c, soft white=#f2efe9, grey=#9a9ca3, mauve=#a77c8e"),
        avoid_colours=_swatches("orange=#f08a24, mustard=#d4a017, black=#111111, camel=#c19a6b"),
        metals="silver", face_shape="oval", hair_now="Shoulder-length ash-brown hair worn straight.",
        hair_suggestions=["Soft face-framing layers keep the look light.",
                          "A cool beige balayage adds dimension without warmth.",
                          "Loose waves suit an oval face and soften the jaw line."],
        makeup_suggestions=["Light-coverage base with a pink-neutral undertone.",
                            "Rose or berry-tinted blush on the apples of the cheeks.",
                            "Taupe and soft grey eyeshadow; brown-black mascara.",
                            "Rosy nude or mauve lips; avoid orange-based reds."],
        silhouette_notes=None, style_tags=["minimalist", "ballet_core"],
        caveats="Offline demo analysis: not based on your photo.",
    ),
    PersonAnalysis(
        usable=True, colour_season="autumn", season_detail="deep autumn", undertone="warm", contrast="medium",
        colouring_notes="Dark brown hair with golden warmth, brown eyes and a warm, golden undertone.",
        best_colours=_swatches("camel=#c19a6b, olive=#6b6b2f, rust=#b7410e, chocolate brown=#5a3825, "
                               "cream=#f3e9d2, forest green=#2e5339, mustard=#d4a017, beige=#d8c3a5"),
        avoid_colours=_swatches("icy pink=#f6d6e3, bright white=#ffffff, fuchsia=#d1338b, powder blue=#a7c4dc"),
        metals="gold", face_shape="heart", hair_now="Long dark brown hair with a centre part.",
        hair_suggestions=["Curtain bangs balance a heart-shaped face.",
                          "Warm chestnut or caramel highlights bring out golden tones.",
                          "Collarbone length with soft layers adds volume near the jaw."],
        makeup_suggestions=["Warm-golden foundation with a satin finish.",
                            "Peach or terracotta blush.",
                            "Bronze, copper and olive eyeshadow.",
                            "Brick red, warm nude or cinnamon lips."],
        silhouette_notes="Balanced proportions: a defined waist and straight or wide-leg trousers work well.",
        style_tags=["old_money", "boho"],
        caveats="Offline demo analysis: not based on your photo.",
    ),
    PersonAnalysis(
        usable=True, colour_season="winter", season_detail="cool winter", undertone="cool", contrast="high",
        colouring_notes="Very dark hair and eyes against fair, cool-toned skin give strong contrast.",
        best_colours=_swatches("black=#111111, bright white=#ffffff, navy=#1f2a44, emerald=#047857, "
                               "true red=#c8102e, icy pink=#f6d6e3, royal blue=#2747a6, charcoal=#36454f"),
        avoid_colours=_swatches("beige=#d8c3a5, orange=#f08a24, olive=#6b6b2f, camel=#c19a6b"),
        metals="silver", face_shape="square", hair_now="Black hair in a sleek chin-length bob.",
        hair_suggestions=["Soft side-swept layers round off a square jaw.",
                          "Keep a cool blue-black or espresso shade.",
                          "A longer bob past the jaw softens strong angles."],
        makeup_suggestions=["Neutral-to-cool base with a natural finish.",
                            "Cool pink or plum blush.",
                            "Crisp black liner with grey or silver shadow.",
                            "Blue-red or berry lips."],
        silhouette_notes=None, style_tags=["minimalist", "office"],
        caveats="Offline demo analysis: not based on your photo.",
    ),
    PersonAnalysis(
        usable=True, colour_season="spring", season_detail="light spring", undertone="warm", contrast="low",
        colouring_notes="Light golden-brown hair, light eyes and a peachy undertone.",
        best_colours=_swatches("peach=#f6b48f, coral=#f88379, warm ivory=#fff4e0, light camel=#d6b88f, "
                               "aqua=#7fd1c7, butter yellow=#f8e08e, light pink=#f7c6c7, warm beige=#e3cba8"),
        avoid_colours=_swatches("black=#111111, burgundy=#6d1a36, charcoal=#36454f, icy blue=#d6ecf5"),
        metals="gold", face_shape="round", hair_now="Light golden-brown hair just past the shoulders.",
        hair_suggestions=["Long layers that start below the chin lengthen a round face.",
                          "Honey or strawberry-blonde highlights keep it bright.",
                          "A side part adds height and angles."],
        makeup_suggestions=["Sheer, warm-ivory base.",
                            "Peach or coral blush.",
                            "Champagne and soft bronze eyeshadow with brown mascara.",
                            "Coral, peach or warm pink lips."],
        silhouette_notes=None, style_tags=["coquette", "resort"],
        caveats="Offline demo analysis: not based on your photo.",
    ),
]


class FakeVision:
    name = "fake"

    def analyze_look(self, image: bytes, media_type: str) -> LookAnalysis:
        idx = int.from_bytes(hashlib.sha256(image).digest()[:4], "little") % len(_FAKE_LOOKS)
        return _FAKE_LOOKS[idx].model_copy(deep=True)

    def analyze_person(self, photos: list[tuple[bytes, str]]) -> PersonAnalysis:
        digest = hashlib.sha256(b"".join(data for data, _ in photos)).digest()
        person = _FAKE_PEOPLE[int.from_bytes(digest[:4], "little") % len(_FAKE_PEOPLE)].model_copy(deep=True)
        # Offline: a lone photo counts as full body; with several, the first is the selfie.
        person.photo_checks = [
            PhotoCheck(framing="face", good_for_colour=True, good_for_tryon=False, tip="Good selfie for your colours.")
            if i == 0 and len(photos) > 1 else
            PhotoCheck(framing="full_body", good_for_colour=True, good_for_tryon=True, tip="Ready for try-on.")
            for i in range(len(photos))
        ]
        return person


    def check_photo(self, image: bytes, media_type: str) -> PhotoCheck:
        # Offline there is nobody to look at the photo, so it is accepted.
        return PhotoCheck(framing="full_body", good_for_colour=True, good_for_tryon=True, tip="Ready for try-on.")

    def curate_outfits(self, request: StylingRequest) -> StylingResult:
        # Offline: keep the scorer's top pick per slot and explain it from the request alone.
        outfits = []
        for o in request.outfits:
            picks = [s.candidates[0] for s in o.slots if s.candidates]
            accent = next((c for c, s in zip(picks, o.slots) if s.role == "accent"), None)
            base = sorted({c.colour.lower() for c, s in zip(picks, o.slots) if s.role == "neutral" and c.colour})
            why = (f"{accent.colour.capitalize()} pops" if accent and accent.colour else "Calm and easy") + (
                f" against {' and '.join(base[:2])}" if base else "") + f", very {o.style.lower()}."
            outfits.append(StyledOutfit(index=o.index, picks=[c.id for c in picks], approved=True, why=why))
        return StylingResult(outfits=outfits)


def make_vision_llm(api_key: str, model: str, timeout_s: float) -> VisionLLM:
    if api_key:
        return ClaudeVision(api_key, model, timeout_s)
    log.warning("ANTHROPIC_API_KEY not set: using the offline FakeVision model")
    return FakeVision()
