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


def plan_steps(pieces: list[dict]) -> list[dict]:
    """Which pieces of an outfit to render, in order. At most two calls per outfit.

    A dress is one step. Otherwise the bottom goes first, then one upper-body piece: the
    outerwear if there is one (it's what you see), else the top.
    """
    by_cat = {p["category"]: p for p in pieces}
    if "dress" in by_cat:
        return [by_cat["dress"]]
    upper = by_cat.get("outerwear") or by_cat.get("top")
    return [p for p in (by_cat.get("bottom"), upper) if p]


TRYON_STAGE = "tryon:stage:{}"  # what a running try-on is doing now, for the UI
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
Keep it the same person: the same face, skin tone, hair, and their real body shape and proportions. {size}
Pose: facing the camera, standing straight, arms relaxed at the sides, the whole body visible from head to toe.
Clothes: a plain white tank top, light blue denim shorts and white sneakers, nothing else.
Background: a plain light-grey studio backdrop with soft, even light, like a shop's try-on base model.
Do not slim, reshape or beautify the body: no thinner waist, longer legs, smaller arms, smoother skin or
retouched face. The result must look like this person on an ordinary day, just in basics against a plain wall."""


MODEL_FACE_LINE = """Image 2 is a close-up of the same person's face: the face in the result must match it exactly
(eyes, nose, mouth, face shape, skin tone). Do not beautify or change it."""


def model_prompt(height_cm: float | None = None, weight_kg: float | None = None, face: bool = False) -> str:
    """The "My model" prompt, with the profile's height and weight as hints for the body's real size, and
    with `face`, a line about the face close-up sent as image 2."""
    hints = []
    if height_cm:
        hints.append(f"{height_cm:g} cm tall")
    if weight_kg:
        hints.append(f"{weight_kg:g} kg")
    size = f"For reference they are {' and '.join(hints)}; draw that build as it is." if hints else ""
    prompt = "\n".join(line.rstrip() for line in MODEL_PROMPT.format(size=size).splitlines())
    return f"{prompt}\n{MODEL_FACE_LINE}" if face else prompt


FACE_REFERENCE = """Image 2 is a close-up of this same person's face and hair: the face, hairstyle and hair length
in the result must match it exactly (same hairstyle and hair length: a chin-length bob stays a chin-length bob,
short hair stays short). Use it only for the face and hair, never for the clothes."""


def nano_banana_prompt(shown: list[str], described: list[str], face: bool = False) -> str:
    """The try-on prompt; with `face`, image 2 is a close-up of the person's face and the garments follow it."""
    prompt = NANO_BANANA_PROMPT.format(pieces=nano_banana_pieces(shown, described, "images 3 onward" if face else None))
    return f"{prompt}\n{FACE_REFERENCE}" if face else prompt


def nano_banana_pieces(shown: list[str], described: list[str], where: str | None = None) -> str:
    """The outfit part of the prompt: pieces with a photo, then pieces known only by their description."""
    parts = []
    if shown:
        parts.append(f"the clothes and accessories shown in {where or 'the other images'}: " + "; ".join(shown))
    if described:
        parts.append("these pieces, which have no photo, as described: " + "; ".join(described))
    return ", and ".join(parts)


class NanoBananaTryOn(ReplicateTryOn):
    """Google's Nano Banana image model, an official model on Replicate: always warm, and one call
    dresses the whole outfit (shoes and bags included), so there are no per-garment steps."""

    name = "nano-banana"
    whole_outfit = True
    takes_face = True  # dress_outfit accepts a face close-up (My model try-ons)

    def __init__(self, token: str, model: str = NANO_BANANA, timeout_s: float = 150.0, http: httpx.Client | None = None):
        super().__init__(token, model, timeout_s, http)

    def dress_outfit(self, person: bytes, media_type: str, garments: list[Garment],
                     face: tuple[bytes, str] | None = None) -> tuple[bytes, str]:
        """`face`: a close-up of the person's head (see tryon.face), sent as image 2 so the face stays theirs."""
        # Only photos we fetched ourselves go in. A shop URL would make the model fetch it, and a CDN that
        # stalled us usually stalls it too, failing the whole outfit; such a piece is described in words.
        shown = [g for g in garments if g.image]
        described = [g for g in garments if not g.image]
        images = [self._file_input(person, media_type)] + ([self._file_input(*face)] if face else []) \
            + [self._file_input(g.image, g.media_type) for g in shown]
        body = {"input": {
            "prompt": nano_banana_prompt([g.description for g in shown], [g.description for g in described],
                                         face=face is not None),
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
FASHN_CATEGORIES = {"upper_body": "tops", "lower_body": "bottoms", "dresses": "one-pieces"}


class FashnTryOn:
    """FASHN's hosted try-on model (https://docs.fashn.ai): no cold starts, seconds per garment."""

    name = "fashn"
    renders = True

    def __init__(self, api_key: str, timeout_s: float = 90.0, http: httpx.Client | None = None):
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
        body = {"model_name": "tryon-v1.6", "inputs": {
            "model_image": _data_uri(person, media_type),
            "garment_image": _data_uri(garment.image, garment.media_type) if garment.image else garment.url,
            "category": FASHN_CATEGORIES.get(garment.region, "auto"),
            "mode": "balanced",
            "output_format": "jpeg",
        }}
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


def make_tryon(token: str, model: str = NANO_BANANA, fashn_key: str = ""):
    if fashn_key:
        return FashnTryOn(fashn_key)
    if not token:
        return PreviewTryOn()
    return NanoBananaTryOn(token, model) if model.startswith(NANO_BANANA) else ReplicateTryOn(token, model)
