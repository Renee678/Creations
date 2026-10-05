"""Virtual try-on: dress the user's own photo in catalog pieces.

The image model is IDM-VTON (open source, https://github.com/yisol/IDM-VTON), run on Replicate so
no GPU is needed locally. It swaps one garment per call, so an outfit is applied step by step:
the output of one step is the person photo for the next.

Without REPLICATE_API_TOKEN the app uses PreviewTryOn, which returns the photo unchanged; the UI
then shows the outfit pinned next to the photo (a collage) instead of a rendered try-on.
"""

import base64
import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

REPLICATE_API = "https://api.replicate.com/v1"
DEFAULT_MODEL = "cuuupid/idm-vton"

# App category -> IDM-VTON garment region. Shoes, bags and accessories can't be rendered.
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

    def __init__(self, token: str, model: str = DEFAULT_MODEL, timeout_s: float = 420.0,
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
        deadline = time.monotonic() + self.timeout_s
        while pred["status"] in ("starting", "processing"):
            if time.monotonic() > deadline:
                raise TryOnError("try-on timed out", retryable=True)
            time.sleep(2)
            pred = self._request("GET", pred["urls"]["get"])
        if pred["status"] != "succeeded":
            raise TryOnError(f"try-on {pred['status']}: {pred.get('error') or 'no output'}")
        output = pred["output"][0] if isinstance(pred["output"], list) else pred["output"]
        try:
            img = self.http.get(output)
            img.raise_for_status()
        except httpx.HTTPError as e:
            raise TryOnError(f"couldn't download the try-on image: {e}", retryable=True) from e
        return img.content, img.headers.get("content-type", "image/png").split(";")[0]


def make_tryon(token: str, model: str = DEFAULT_MODEL):
    return ReplicateTryOn(token, model) if token else PreviewTryOn()
