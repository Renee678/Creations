"""Feedback #38: when a shop CDN refuses our server, the shopper's browser loads the photo and hands it over."""

import io
import logging

import pytest
from PIL import Image

from lookmate.catalog.service import ProductView
from lookmate.tryon.garments import BROWSER_PHOTO_MAX_BYTES, cached_photo, store_browser_photo

URL = "https://images.asos-media.com/products/browser/1"


def picture(fmt="JPEG", size=(300, 400)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, fmt)
    return out.getvalue()


@pytest.fixture
def piece(runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "data_dir", tmp_path)
    p = ProductView("asos-browser-1", "Satin midi skirt", "Skirts", "bottom", "black", "", URL, 30)
    runtime.catalog.products[p.id] = p
    yield p
    del runtime.catalog.products[p.id]


def test_a_photo_from_the_browser_goes_into_the_try_on_cache(client, runtime, piece):
    jpg = picture()
    res = client.post(f"/api/products/{piece.id}/photo", content=jpg, headers={"content-type": "image/jpeg"})
    assert res.status_code == 200 and res.json() == {"status": "stored"}
    assert cached_photo(URL, runtime.data_dir) == (jpg, "image/jpeg"), "keyed by the piece's photo, type sniffed"
    again = client.post(f"/api/products/{piece.id}/photo", content=picture("PNG"))
    assert again.json() == {"status": "kept"}, "an upload never replaces a cached photo"
    assert cached_photo(URL, runtime.data_dir)[1] == "image/jpeg"


def test_the_worker_uses_a_photo_the_browser_handed_over(client, runtime, user, piece, monkeypatch):
    from lookmate import worker
    from lookmate.tryon.client import Garment
    from tests.test_tryon import pick, request_tryon

    class Outfit:
        name, renders, whole_outfit = "nano-banana", True, True
        garments = []

        def dress_outfit(self, person, media_type, garments):
            self.garments = garments
            return b"out", "image/jpeg"

    model = Outfit()
    monkeypatch.setattr(runtime, "tryon", model)
    # The server's own fetch is refused (as the ASOS CDN refuses the Hetzner server).
    monkeypatch.setattr(worker, "garment_for", lambda p, data_dir: (
        Garment(b"img", "image/jpeg", "upper_body", p.name) if p.id != piece.id else worker.garment_from_words(p)))
    jpg = picture()
    client.post(f"/api/products/{piece.id}/photo", content=jpg)
    tid = request_tryon(client, user["id"], pick(runtime, "top") + [piece.id]).json()["id"]
    assert worker.process_tryon(runtime, tid) == "done"
    assert [g.image for g in model.garments] == [b"img", jpg]


def test_uploads_are_checked(client, runtime, piece):
    post = lambda body: client.post(f"/api/products/{piece.id}/photo", content=body)  # noqa: E731
    assert post(b"<html>not a photo</html>").status_code == 415
    assert post(picture(size=(40, 40))).status_code == 415, "a tracking pixel isn't a product photo"
    assert post(b"\xff\xd8\xff" + b"0" * BROWSER_PHOTO_MAX_BYTES).status_code == 413
    assert cached_photo(URL, runtime.data_dir) is None
    assert client.post("/api/products/not-a-piece/photo", content=picture()).status_code == 404, "catalog pieces only"
    gif = post(picture("GIF"))
    assert gif.json() == {"status": "stored"} and cached_photo(URL, runtime.data_dir)[1] == "image/jpeg", "converted"


def test_a_browser_that_was_blocked_too_is_logged(client, runtime, piece, caplog):
    with caplog.at_level(logging.INFO, logger="lookmate.api.tryon"):
        res = client.post(f"/api/products/{piece.id}/photo?blocked=true")
    assert res.json() == {"status": "blocked"} and "browser fetch blocked by CORS" in caplog.text
    assert cached_photo(URL, runtime.data_dir) is None


def test_store_browser_photo_needs_a_cache_dir(tmp_path):
    assert store_browser_photo(URL, tmp_path, picture()) == "stored"
    assert store_browser_photo(URL, tmp_path, picture()) == "kept"
