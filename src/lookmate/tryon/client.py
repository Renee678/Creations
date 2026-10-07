"""Virtual try-on: dress the user's own photo in catalog pieces.

Three image models are supported. The per-garment ones swap one garment per call, so an outfit is
applied step by step (the output of one step is the person photo for the next):

- FASHN try-on (hosted API, FASHN_API_KEY): always warm, about 5-10 seconds a garment, about $0.075
  an image. Preferred when configured, because a demo can't wait for a cold GPU.
- Nano Banana (Google's image model, official on Replicate; the default with REPLICATE_API_TOKEN):
  always warm, and one call dresses the whole outfit, shoes and bags included.
- IDM-VTON (open source, https://github.com/yisol/IDM-VTON) on Replicate (TRYON_MODEL=cuuupid/idm-vton):
  about $0.02 a run, but a community model that has gone idle can take minutes to boot.

Without either, the app uses PreviewTryOn, which returns the photo unchanged; the UI then shows the
outfit pinned next to the photo (a collage) instead of a rendered try-on.
"""

import base64
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

TRANSIENT = re.compile(r"overloaded|unavailable|temporar|timed? ?out|rate.?limit|try again|\b(429|500|502|503|504)\b", re.I)

REPLICATE_API = "https://api.replicate.com/v1"
DEFAULT_MODEL = "cuuupid/idm-vton"

# App category -> IDM-VTON garment region. Shoes, bags and accessories can't be rendered by the
# per-garment models; Nano Banana renders them too ("accessory").
REGIONS = {"top": "upper_body", "outerwear": "upper_body", "bottom": "lower_body", "dress": "dresses"}


class TryOnError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Garment:
    image: bytes | None  # None: the model fetches `url` itself
    media_type: str
    region: str        # upper_body | lower_body | dresses
    description: str   # e.g. "sage green satin slip dress"
    url: str | None = None


def plan_steps(pieces: list[dict], extras: tuple[str, ...] = ()) -> list[dict]:
    """Which pieces of an outfit to render, one model call each, in the order they are put on.

    Every clothing layer: a dress, or the bottom then the top; then the outerwear over it. A cropped top (or a
    cropped jacket with no top) goes on first and the bottom last. Then the `extras` the model can also draw (FASHN Try-On Max: shoes). At most four calls per outfit.
    """
    by_cat = {p["category"]: p for p in pieces}
    if "dress" in by_cat:
        order = ["dress", "outerwear"]
    elif _cropped(by_cat.get("top") or by_cat.get("outerwear")):
        # A cropped top leaves the waist bare: put on over My model's denim shorts it kept their frayed hem, or the
        # shop photo's jeans, as a blurry band under the hem (Renee, 2026-10-07). With the trousers last, their own
        # waistband is drawn into that gap.
        order = ["top", "outerwear", "bottom"]
    else:
        order = ["bottom", "top", "outerwear"]
    return [by_cat[c] for c in (*order, *extras) if c in by_cat]


CROPPED_TOP = re.compile(r"\bcrop(ped)?\b", re.I)
CROPPED_FIT = re.compile(r"\bcrop(ped)? (length|fit|cut|hem|style)\b", re.I)  # ASOS: "Cropped length" in the details


def _cropped(piece: dict | None) -> bool:
    product = (piece or {}).get("product")
    return bool(product and (CROPPED_TOP.search(f"{product.name} {product.product_type}")
                             or CROPPED_FIT.search(getattr(product, "description", "") or "")))


TRYON_STAGE = "tryon:stage:{}"  # what a running try-on is doing now, for the UI


def tryon_quota_key() -> str:
    """Today's count of paid try-on model runs (the daily cap)."""
    return f"quota:tryons:{datetime.now(timezone.utc):%Y-%m-%d}"
DATA_URI_MAX_BYTES = 256 * 1024  # Replicate's limit for inline data URIs; bigger files are uploaded first


def _data_uri(data: bytes, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode()}"


class PreviewTryOn:
    """No image model configured: hand the photo back so the UI can build a collage."""

    name = "preview"
    renders = False

    def dress(self, person: bytes, media_type: str, garment: Garment) -> tuple[bytes, str]:
        return person, media_type


class ReplicateTryOn:
    name = "idm-vton"
    renders = True
    fetches_urls = True  # takes a garment photo by URL and loads it itself

    def __init__(self, token: str, model: str = DEFAULT_MODEL, timeout_s: float = 240.0,
                 http: httpx.Client | None = None):
        self.model = model
        self.timeout_s = timeout_s
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, read=90))
        self.headers = {"Authorization": f"Bearer {token}"}
        self._version: str | None = None

    def _request(self, method: str, url: str, **kw) -> dict:
        try:
            r = self.http.request(method, url, headers=self.headers | kw.pop("headers", {}), **kw)
        except httpx.TransportError as e:
            raise TryOnError(f"try-on service unreachable: {e}", retryable=True) from e
        if r.status_code == 401:
            raise TryOnError("Replicate rejected the token: check REPLICATE_API_TOKEN in .env")
        if r.status_code == 402:
            raise TryOnError("Replicate needs billing set up before it runs models (replicate.com/account/billing)")
        if r.status_code == 429 or r.status_code >= 500:
            raise TryOnError(f"try-on service busy ({r.status_code})", retryable=True)
        if r.status_code >= 400:
            raise TryOnError(f"try-on request failed ({r.status_code}): {r.text[:200]}")
        return r.json()

    def version(self) -> str:
        """The model's latest version, looked up once, so no version hash is hard-coded."""
        if self._version is None:
            info = self._request("GET", f"{REPLICATE_API}/models/{self.model}")
            self._version = info["latest_version"]["id"]
        return self._version

    def _file_input(self, data: bytes, media_type: str) -> str:
        if len(data) <= DATA_URI_MAX_BYTES:
            return _data_uri(data, media_type)
        ext = media_type.split("/")[-1]
        uploaded = self._request("POST", f"{REPLICATE_API}/files",
                                 files={"content": (f"input.{ext}", data, media_type)})
        return uploaded["urls"]["get"]

    def dress(self, person: bytes, media_type: str, garment: Garment) -> tuple[bytes, str]:
        body = {"version": self.version(), "input": {
            "human_img": self._file_input(person, media_type),
            "garm_img": self._file_input(garment.image, garment.media_type) if garment.image else garment.url,
            "garment_des": garment.description,
            "category": garment.region,
            "crop": True,  # accept photos that aren't 3:4
        }}
        pred = self._request("POST", f"{REPLICATE_API}/predictions", json=body, headers={"Prefer": "wait=60"})
        return self._finish(pred)

    def _finish(self, pred: dict) -> tuple[bytes, str]:
        """Wait for a prediction, cancelling it past the timeout, and download its image."""
        deadline = time.monotonic() + self.timeout_s
        while pred["status"] in ("starting", "processing"):
            if time.monotonic() > deadline:
                # Cancel rather than retry: a retry would start (and pay for) a second prediction,
                # and the user has already waited long enough.
                try:
                    self._request("POST", pred["urls"]["cancel"])
                except TryOnError:
                    pass
                raise TryOnError("The try-on model took too long to start. Please try again in a minute.")
            time.sleep(2)
            pred = self._request("GET", pred["urls"]["get"])
        if pred["status"] != "succeeded":
            log.warning("try-on prediction %s %s: %s", pred.get("id"), pred["status"], pred.get("error"))
            # A busy or briefly unavailable model is worth one more attempt (the worker retries with backoff);
            # anything else, e.g. a refused image, fails at once.
            busy = bool(TRANSIENT.search(str(pred.get("error") or "")))
            raise TryOnError("The try-on model couldn't render this outfit. Please try again, or swap a piece.",
                             retryable=busy)
        output = pred["output"][0] if isinstance(pred["output"], list) else pred["output"]
        try:
            img = self.http.get(output)
            img.raise_for_status()
        except httpx.HTTPError as e:
            raise TryOnError(f"couldn't download the try-on image: {e}", retryable=True) from e
        return img.content, img.headers.get("content-type", "image/png").split(";")[0]


NANO_BANANA = "google/nano-banana"
NANO_BANANA_PRO = "google/nano-banana-pro"
NANO_BANANA_PROMPT = """Edit the first image, a photo of a person. Change only their clothes: dress them in {pieces},
replacing what they are wearing now, and keep each piece's colour, pattern, fabric and cut.
Everything else stays exactly as in the first photo: the same face, hair, skin tone, head size, height, body
proportions and build, the same pose, the same camera angle, crop and framing, and the same background and light.
Fit each garment to this person's body at their real size, the way it would drape on them. Do not make their
silhouette wider, taller or slimmer, do not shrink or enlarge their head, and do not copy the size or pose of
any model in the other images. The result must look like the original photo, only with different clothes."""


MODEL_PROMPT = """Turn this photo into a clean full-body base photo of the same person, for a virtual fitting room.
Keep it the same person: the same face, skin tone and hair.
{body}
Proportions: a real adult's, about 7 to 7.5 head heights from the top of the head to the soles, legs (hip
to floor) about half the height. Do not draw the head too large or the legs short: that reads as shorter.
Pose: facing the camera, standing straight, arms relaxed at the sides, the whole body visible from head to toe.
Clothes: a plain white tank top, light blue denim shorts and white sneakers, nothing else.
No sunglasses, glasses, hat, jewellery, bag or other accessories, even if the photo has them: a bare face
with the eyes visible.
Background: a plain light-grey studio backdrop with soft, even light, like a shop's try-on base model.
Draw the body neither slimmer nor heavier than that: do not reshape or beautify it, and do not retouch the face.
The result must look like this person on an ordinary day, just in basics against a plain wall."""

# With the profile's height and weight, the numbers set the body's size: a photo in a baggy top or wide-leg
# trousers looks heavier than the person is (Renee, 2026-10-07).
BODY_FROM_NUMBERS = """Body: they are {height:g} cm tall and {weight:g} kg, {build}. Draw the typical body of a person of
that height and weight: the numbers decide the body's size, whatever the photo seems to show. Loose tops, wide-leg
trousers or long skirts hide the waist and legs, so never draw them wider than the numbers say. From the photo take
only the face, skin tone, hair and the shoulder width you can see."""

BODY_FROM_PHOTO = """Body: keep their real body shape and proportions{size}. Loose or bulky clothes hide the body:
judge the build from the face, neck, arms and legs, never from the outline of the clothes."""


MODEL_FACE_LINE = """Image 2 is a close-up of the same person's face: the face in the result must match it exactly
(eyes, nose, mouth, face shape, skin tone). Do not beautify or change it."""


def build_word(height_cm: float, weight_kg: float) -> str:
    """Height and weight as the plain build an image model can draw: 166 cm and 55 kg is "slim"."""
    bmi = weight_kg / (height_cm / 100) ** 2
    word = ("very slim" if bmi < 18.5 else "slim" if bmi < 22 else "average" if bmi < 25
            else "curvy, a little fuller" if bmi < 30 else "plus-size")
    return f"{'an' if word[0] in 'aeiou' else 'a'} {word} build (BMI {bmi:.0f})"


def model_prompt(height_cm: float | None = None, weight_kg: float | None = None, face: bool = False) -> str:
    """The "My model" prompt. With the profile's height and weight, those set the body's size and the photo gives
    the face, hair and proportions; without both, the photo's body is kept. With `face`, a line about the face
    close-up sent as image 2."""
    if height_cm and weight_kg:
        body = BODY_FROM_NUMBERS.format(height=height_cm, weight=weight_kg, build=build_word(height_cm, weight_kg))
    else:
        known = f" ({height_cm:g} cm tall)" if height_cm else f" ({weight_kg:g} kg)" if weight_kg else ""
        body = BODY_FROM_PHOTO.format(size=known)
    prompt = "\n".join(line.rstrip() for line in MODEL_PROMPT.format(body=body).splitlines())
    return f"{prompt}\n{MODEL_FACE_LINE}" if face else prompt


FACE_REFERENCE = """Image 2 is a close-up of this same person's face and hair: the face, hairstyle and hair length
in the result must match it exactly (same hairstyle and hair length: a chin-length bob stays a chin-length bob,
short hair stays short). Use it only for the face and hair, never for the clothes."""


def nano_banana_prompt(shown: list[str], face: bool = False) -> str:
    """The try-on prompt; with `face`, image 2 is a close-up of the person's face and the garments follow it."""
    prompt = NANO_BANANA_PROMPT.format(pieces=nano_banana_pieces(shown, "images 3 onward" if face else None))
    return f"{prompt}\n{FACE_REFERENCE}" if face else prompt


def nano_banana_pieces(shown: list[str], where: str | None = None) -> str:
    """The outfit part of the prompt: every piece is shown in a photo (none is drawn from words)."""
    return f"the clothes and accessories shown in {where or 'the other images'}: " + "; ".join(shown)


class NanoBananaTryOn(ReplicateTryOn):
    """Google's Nano Banana image model, an official model on Replicate: always warm, and one call
    dresses the whole outfit (shoes and bags included), so there are no per-garment steps."""

    name = "nano-banana"
    whole_outfit = True
    fetches_urls = False  # only photos we fetched go in (see dress_outfit)
    takes_face = True  # dress_outfit accepts a face close-up (My model try-ons)

    def __init__(self, token: str, model: str = NANO_BANANA, timeout_s: float = 150.0, http: httpx.Client | None = None):
        super().__init__(token, model, timeout_s, http)

    def dress_outfit(self, person: bytes, media_type: str, garments: list[Garment],
                     face: tuple[bytes, str] | None = None) -> tuple[bytes, str]:
        """`face`: a close-up of the person's head (see tryon.face), sent as image 2 so the face stays theirs."""
        # Only photos we fetched ourselves go in, and never a piece described in words instead: a garment
        # drawn from text comes out wrong (the worker stops the try-on before it gets here).
        if any(not g.image for g in garments):
            raise TryOnError("A piece has no photo, so it can't be tried on.")
        shown = garments
        images = [self._file_input(person, media_type)] + ([self._file_input(*face)] if face else []) \
            + [self._file_input(g.image, g.media_type) for g in shown]
        body = {"input": {
            "prompt": nano_banana_prompt([g.description for g in shown], face=face is not None),
            "image_input": images,
            "aspect_ratio": "match_input_image",  # keep the person's own framing
            "output_format": "jpg",
        }}
        # Official models take predictions on the model itself, without a version id.
        pred = self._request("POST", f"{REPLICATE_API}/models/{self.model}/predictions", json=body,
                             headers={"Prefer": "wait=60"})
        return self._finish(pred)

    def dress(self, person: bytes, media_type: str, garment: Garment) -> tuple[bytes, str]:
        return self.dress_outfit(person, media_type, [garment])

    def make_model(self, person: bytes, media_type: str, height_cm: float | None = None,
                   weight_kg: float | None = None, face: tuple[bytes, str] | None = None) -> tuple[bytes, str]:
        """"My model": the same person, standing front-on in plain basics on a grey studio background.
        `face`: an optional close-up of the face the user uploaded, sent as image 2."""
        body = {"input": {
            "prompt": model_prompt(height_cm, weight_kg, face=face is not None),
            "image_input": [self._file_input(person, media_type)] + ([self._file_input(*face)] if face else []),
            "aspect_ratio": "3:4",  # a standing full-body frame, whatever the upload's crop
            "output_format": "jpg",
        }}
        pred = self._request("POST", f"{REPLICATE_API}/models/{self.model}/predictions", json=body,
                             headers={"Prefer": "wait=60"})
        return self._finish(pred)


FASHN_API = "https://api.fashn.ai/v1"
# Tops worn open over something, or two pieces in one. Laid over My model's own tank top they let it show through
# the opening and left a seam under the arm (a cami with a bolero, Renee 2026-10-07), so for these FASHN takes the
# base clothes off first. Everything else keeps the segmentation-free fit, which keeps the body and skin as they are.
OPEN_LAYERED = re.compile(r"\b(bolero|shrug|kimono|2[- ]in[- ]1|two[- ]in[- ]one|open[- ]front|layered|"
                          r"double layer|cape)\b", re.I)
FASHN_CATEGORIES = {"upper_body": "tops", "lower_body": "bottoms", "dresses": "one-pieces"}


FASHN_MAX = "tryon-max"
FASHN_CLOTHES = "tryon-v1.6"


def _photo_type(garment: Garment) -> str:
    """FASHN's garment_photo_type: ASOS shoots every piece on a model; anything else, FASHN decides."""
    return "model" if garment.url and "asos-media.com/" in garment.url else "auto"


class FashnTryOn:
    """FASHN's hosted try-on (https://docs.fashn.ai): no cold starts, one garment per call.

    Clothes always go through tryon-v1.6 with their category (tops, bottoms, one-pieces), so a step puts on
    only that piece. With FASHN_MODEL=tryon-max (the default) the shoes go on last through Try-On Max;
    with tryon-v1.6 shoes aren't drawn.
    """

    name = "fashn"
    renders = True
    fetches_urls = True  # takes a garment photo by URL and loads it itself

    def __init__(self, api_key: str, model: str = FASHN_MAX, timeout_s: float = 90.0,
                 http: httpx.Client | None = None, mode: str = "quality"):
        self.model = model
        self.mode = mode  # FASHN_MODE: "performance", "balanced" or "quality", per clothing step
        self.extra_steps = ("shoes",) if model == FASHN_MAX else ()
        self.timeout_s = timeout_s
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, read=60))
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def _request(self, method: str, url: str, **kw) -> dict:
        try:
            r = self.http.request(method, url, headers=self.headers, **kw)
        except httpx.TransportError as e:
            raise TryOnError(f"try-on service unreachable: {e}", retryable=True) from e
        if r.status_code == 401:
            raise TryOnError("FASHN rejected the key: check FASHN_API_KEY in .env")
        if r.status_code == 402:
            raise TryOnError("FASHN is out of credits (app.fashn.ai)")
        if r.status_code == 429 or r.status_code >= 500:
            raise TryOnError(f"try-on service busy ({r.status_code})", retryable=True)
        if r.status_code >= 400:
            raise TryOnError(f"try-on request failed ({r.status_code}): {r.text[:200]}")
        return r.json()

    def dress(self, person: bytes, media_type: str, garment: Garment) -> tuple[bytes, str]:
        product = _data_uri(garment.image, garment.media_type) if garment.image else garment.url
        if garment.region in FASHN_CATEGORIES:
            # Clothes go through tryon-v1.6, locked to the piece's category: Try-On Max has no category, and
            # from an on-model shop photo it copied the model's other clothes (a cardigan came back as the
            # skirt worn with it). ASOS photos are on a model; others are left to FASHN to tell.
            # FASHN's default segmentation-free fit, on My model in fitted basics. Segmenting the clothes out first
            # left the gap under a cropped cardigan to be guessed, and it painted a denim band from the shop photo
            # (Renee, 2026-10-07). The mode is FASHN_MODE: quality by default, the closest to a real photo.
            body = {"model_name": FASHN_CLOTHES, "inputs": {
                "model_image": _data_uri(person, media_type), "garment_image": product,
                "category": FASHN_CATEGORIES[garment.region], "garment_photo_type": _photo_type(garment),
                "mode": self.mode, "output_format": "jpeg"}}
            if garment.region == "upper_body" and OPEN_LAYERED.search(garment.description):
                body["inputs"]["segmentation_free"] = False
        else:  # shoes: a packshot, which Try-On Max puts on as it is
            body = {"model_name": FASHN_MAX, "inputs": {
                "model_image": _data_uri(person, media_type), "product_image": product,
                "generation_mode": "balanced", "output_format": "jpeg"}}
        run = self._request("POST", f"{FASHN_API}/run", json=body)
        if run.get("error") or not run.get("id"):
            raise TryOnError(f"try-on was not accepted: {run.get('error')}")
        deadline = time.monotonic() + self.timeout_s
        status = {"status": "starting"}
        while status["status"] in ("starting", "in_queue", "processing"):
            if time.monotonic() > deadline:
                raise TryOnError("The try-on took too long. Please try again.")
            time.sleep(1.5)
            status = self._request("GET", f"{FASHN_API}/status/{run['id']}")
        if status["status"] != "completed" or not status.get("output"):
            err = status.get("error")
            msg = err.get("message") if isinstance(err, dict) else err
            raise TryOnError(f"try-on {status['status']}: {msg or 'no output'}")
        try:
            img = self.http.get(status["output"][0])
            img.raise_for_status()
        except httpx.HTTPError as e:
            raise TryOnError(f"couldn't download the try-on image: {e}", retryable=True) from e
        return img.content, img.headers.get("content-type", "image/jpeg").split(";")[0]


class ModelMaker:
    """Draws "My model" with the better model (Nano Banana Pro by default: once per user, so worth its cost),
    falling back to plain Nano Banana if that call fails."""

    def __init__(self, token: str, model: str = NANO_BANANA_PRO, fallback: str = NANO_BANANA,
                 http: httpx.Client | None = None):
        self.primary = NanoBananaTryOn(token, model, http=http)
        self.fallback = NanoBananaTryOn(token, fallback, http=http) if fallback and fallback != model else None
        self.name = model

    def make_model(self, person: bytes, media_type: str, height_cm: float | None = None,
                   weight_kg: float | None = None, face: tuple[bytes, str] | None = None) -> tuple[bytes, str, str]:
        """(image, media type, the model that drew it)."""
        try:
            return (*self.primary.make_model(person, media_type, height_cm, weight_kg, face), self.primary.model)
        except TryOnError as e:
            if self.fallback is None:
                raise
            log.warning("my model: %s failed (%s); trying %s", self.primary.model, e, self.fallback.model)
            return (*self.fallback.make_model(person, media_type, height_cm, weight_kg, face), self.fallback.model)


def make_model_maker(token: str, model: str = NANO_BANANA_PRO):
    """Who draws "My model": Nano Banana Pro (falling back to Nano Banana) with a Replicate token, else nobody
    (the photo is then saved as it is)."""
    return ModelMaker(token, model) if token else None


def make_tryon(token: str, model: str = NANO_BANANA, fashn_key: str = "", fashn_model: str = FASHN_MAX,
               fashn_mode: str = "quality"):
    """FASHN whenever FASHN_API_KEY is set (TRYON_MODEL=fashn says the same); else the Replicate TRYON_MODEL."""
    if fashn_key:
        return FashnTryOn(fashn_key, fashn_model or FASHN_MAX, mode=fashn_mode or "quality")
    if model.lower() == "fashn":
        log.warning("TRYON_MODEL=fashn needs FASHN_API_KEY in .env; using %s instead", NANO_BANANA)
        model = NANO_BANANA
    if not token:
        return PreviewTryOn()
    return NanoBananaTryOn(token, model) if model.startswith(NANO_BANANA) else ReplicateTryOn(token, model)
