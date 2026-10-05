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
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

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
            raise TryOnError("The try-on model couldn't render this outfit. Please try again, or swap a piece.")
        output = pred["output"][0] if isinstance(pred["output"], list) else pred["output"]
        try:
            img = self.http.get(output)
            img.raise_for_status()
        except httpx.HTTPError as e:
            raise TryOnError(f"couldn't download the try-on image: {e}", retryable=True) from e
        return img.content, img.headers.get("content-type", "image/png").split(";")[0]


NANO_BANANA = "google/nano-banana"
NANO_BANANA_PROMPT = """The first image is a photo of a person. Dress this same person in {pieces}. Replace what they
are wearing now with exactly these pieces, keeping each piece's colour, pattern, fabric and cut. Keep the person's
face, hair, skin tone, body shape and the background unchanged. Show a natural, realistic full-length photo of them
standing and facing the camera."""


def nano_banana_pieces(shown: list[str], described: list[str]) -> str:
    """The outfit part of the prompt: pieces with a photo, then pieces known only by their description."""
    parts = []
    if shown:
        parts.append("the clothes and accessories shown in the other images: " + "; ".join(shown))
    if described:
        parts.append("these pieces, which have no photo, as described: " + "; ".join(described))
    return ", and ".join(parts)


class NanoBananaTryOn(ReplicateTryOn):
    """Google's Nano Banana image model, an official model on Replicate: always warm, and one call
    dresses the whole outfit (shoes and bags included), so there are no per-garment steps."""

    name = "nano-banana"
    whole_outfit = True

    def __init__(self, token: str, model: str = NANO_BANANA, timeout_s: float = 150.0, http: httpx.Client | None = None):
        super().__init__(token, model, timeout_s, http)

    def dress_outfit(self, person: bytes, media_type: str, garments: list[Garment]) -> tuple[bytes, str]:
        # Only photos we fetched ourselves go in. A shop URL would make the model fetch it, and a CDN that
        # stalled us usually stalls it too, failing the whole outfit; such a piece is described in words.
        shown = [g for g in garments if g.image]
        described = [g for g in garments if not g.image]
        images = [self._file_input(person, media_type)] + [self._file_input(g.image, g.media_type) for g in shown]
        body = {"input": {
            "prompt": NANO_BANANA_PROMPT.format(pieces=nano_banana_pieces(
                [g.description for g in shown], [g.description for g in described])),
            "image_input": images,
            "output_format": "jpg",
        }}
        # Official models take predictions on the model itself, without a version id.
        pred = self._request("POST", f"{REPLICATE_API}/models/{self.model}/predictions", json=body,
                             headers={"Prefer": "wait=60"})
        return self._finish(pred)

    def dress(self, person: bytes, media_type: str, garment: Garment) -> tuple[bytes, str]:
        return self.dress_outfit(person, media_type, [garment])


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


def make_tryon(token: str, model: str = NANO_BANANA, fashn_key: str = ""):
    if fashn_key:
        return FashnTryOn(fashn_key)
    if not token:
        return PreviewTryOn()
    return NanoBananaTryOn(token, model) if model.startswith(NANO_BANANA) else ReplicateTryOn(token, model)
