import time
"""Virtual try-on: request -> queue -> worker -> image, with the collage preview when no model is set."""

import json
from dataclasses import replace

import httpx
import pytest

from lookmate.tryon.client import FashnTryOn, Garment, PreviewTryOn, ReplicateTryOn, TryOnError, make_tryon, plan_steps
from lookmate.worker import process_job

PHOTO = b"\xff\xd8\xff\xe0" + b"full-body-photo"


def pick(runtime, *categories):
    by_cat = {}
    for p in runtime.catalog.products.values():
        by_cat.setdefault(p.category, p.id)
    return [by_cat[c] for c in categories]


def request_tryon(client, user_id, ids, photo=PHOTO, ctype="image/jpeg"):
    return client.post(f"/api/users/{user_id}/tryons", data={"product_ids": ",".join(ids)},
                       files={"photo": ("me.jpg", photo, ctype)})


def run_next_job(runtime):
    from dataclasses import replace

    job = runtime.tryon_queue.reserve(timeout_s=0.1)
    assert job is not None, "expected a queued try-on"
    return process_job(replace(runtime, queue=runtime.tryon_queue), job)


class FakeRenderer:
    """Stands in for IDM-VTON: appends the garment description to the image bytes."""

    name, renders = "fake-vton", True

    def __init__(self):
        self.calls = []

    def dress(self, person, media_type, garment):
        self.calls.append(garment.region)
        return person + b"|" + garment.region.encode(), "image/png"


@pytest.fixture
def renderer(runtime, monkeypatch):
    fake = FakeRenderer()
    monkeypatch.setattr(runtime, "tryon", fake)
    monkeypatch.setattr("lookmate.worker.garment_for",
                        lambda p, data_dir: Garment(b"img", "image/jpeg", {"dress": "dresses", "bottom": "lower_body"}
                                                    .get(p.category, "upper_body"), p.name))
    return fake


def test_without_a_model_the_photo_comes_back_for_a_collage(client, runtime, user):
    assert client.get("/api/tryon").json() == {"renders": False, "model": "preview"}
    ids = pick(runtime, "top", "bottom", "shoes")
    res = request_tryon(client, user["id"], ids)
    assert res.status_code == 202 and len(res.json()["id"]) == 32, "ids are unguessable tokens"

    assert run_next_job(runtime) == "done"
    out = client.get(f"/api/tryons/{res.json()['id']}").json()
    assert out["result"]["rendered"] is False and out["product_ids"] == ids
    assert client.get(out["image_url"]).content == PHOTO


def test_an_outfit_is_rendered_bottom_first_then_the_upper_piece(client, runtime, user, renderer):
    ids = pick(runtime, "top", "bottom", "outerwear", "shoes")
    tryon_id = request_tryon(client, user["id"], ids).json()["id"]
    assert run_next_job(runtime) == "done"

    out = client.get(f"/api/tryons/{tryon_id}").json()
    assert renderer.calls == ["lower_body", "upper_body"]
    assert out["result"]["rendered"] is True and out["result"]["rendered_ids"] == [ids[1], ids[2]]
    assert client.get(out["image_url"]).content == PHOTO + b"|lower_body|upper_body", "each step builds on the last"


def test_the_photo_is_dropped_after_the_job(client, runtime, user, renderer):
    from lookmate.db import SessionLocal
    from lookmate.models import TryOn

    tryon_id = request_tryon(client, user["id"], pick(runtime, "dress")).json()["id"]
    run_next_job(runtime)
    with SessionLocal() as s:
        assert s.get(TryOn, tryon_id).photo is None


def test_same_photo_and_outfit_is_not_paid_for_twice(client, runtime, user, renderer):
    ids = pick(runtime, "dress")
    first = request_tryon(client, user["id"], ids)
    second = request_tryon(client, user["id"], ids)
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    assert runtime.tryon_queue.depth()["ready"] == 1


def test_busy_model_is_retried(client, runtime, user, renderer, monkeypatch):
    real = renderer.dress
    calls = {"n": 0}

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TryOnError("busy", retryable=True)
        return real(*args)

    monkeypatch.setattr(renderer, "dress", flaky)
    tryon_id = request_tryon(client, user["id"], pick(runtime, "dress")).json()["id"]
    assert run_next_job(runtime) == "queued"
    runtime.tryon_queue.promote_due(now=1e12)
    assert run_next_job(runtime) == "done"
    assert client.get(f"/api/tryons/{tryon_id}").json()["attempts"] == 2


def test_billing_problem_fails_with_a_clear_message(client, runtime, user, renderer, monkeypatch):
    def no_billing(*args):
        raise TryOnError("Replicate needs billing set up before it runs models")

    monkeypatch.setattr(renderer, "dress", no_billing)
    tryon_id = request_tryon(client, user["id"], pick(runtime, "dress")).json()["id"]
    assert run_next_job(runtime) == "failed"
    assert "billing" in client.get(f"/api/tryons/{tryon_id}").json()["error"]


def test_tryon_validation(client, runtime, user):
    top, shoes = pick(runtime, "top", "shoes")
    assert request_tryon(client, user["id"], ["nope"]).status_code == 404
    assert request_tryon(client, user["id"], [shoes]).status_code == 400, "shoes alone can't be rendered"
    assert request_tryon(client, user["id"], [top], ctype="application/pdf").status_code == 415
    assert request_tryon(client, 424242, [top]).status_code == 404


def test_user_can_delete_a_tryon(client, runtime, user):
    tryon_id = request_tryon(client, user["id"], pick(runtime, "dress")).json()["id"]
    run_next_job(runtime)
    assert client.delete(f"/api/tryons/{tryon_id}").status_code == 204
    assert client.get(f"/api/tryons/{tryon_id}").status_code == 404


def test_plan_steps():
    piece = lambda c: {"category": c}  # noqa: E731
    assert plan_steps([piece("dress"), piece("outerwear"), piece("shoes")]) == [piece("dress")]
    assert plan_steps([piece("top"), piece("bottom")]) == [piece("bottom"), piece("top")]
    assert plan_steps([piece("shoes"), piece("bag")]) == []
    assert PreviewTryOn().dress(b"x", "image/png", None) == (b"x", "image/png")


def replicate_stub(statuses, seen):
    """A fake Replicate API: model lookup, a prediction that runs through `statuses`, the output file."""
    state = iter(statuses)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/v1/models/cuuupid/idm-vton":
            return httpx.Response(200, json={"latest_version": {"id": "v123"}})
        if req.url.path == "/v1/files":
            return httpx.Response(201, json={"urls": {"get": "https://api.replicate.com/v1/files/f1"}})
        if req.url.path in ("/v1/predictions", "/v1/predictions/p1"):
            if req.method == "POST":
                seen.append(json.loads(req.content))
            status = next(state)
            out = {"id": "p1", "status": status, "urls": {"get": "https://api.replicate.com/v1/predictions/p1",
                                                          "cancel": "https://api.replicate.com/v1/predictions/p1/cancel"}}
            if status == "succeeded":
                out["output"] = "https://replicate.delivery/out.png"
            if status == "failed":
                out["error"] = "CUDA out of memory"
            return httpx.Response(201 if req.method == "POST" else 200, json=out)
        if req.url.path == "/v1/predictions/p1/cancel":
            return httpx.Response(200, json={"id": "p1", "status": "canceled"})
        if req.url.host == "replicate.delivery":
            return httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_replicate_client_runs_idm_vton_and_downloads_the_result(monkeypatch):
    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    seen = []
    vton = ReplicateTryOn("token", http=replicate_stub(["processing", "succeeded"], seen))
    out = vton.dress(b"person", "image/jpeg", Garment(b"g" * 300_000, "image/jpeg", "upper_body", "black blazer"))

    assert out == (b"PNGDATA", "image/png")
    body = next(s for s in seen if isinstance(s, dict))
    assert body["version"] == "v123" and body["input"]["category"] == "upper_body"
    assert body["input"]["human_img"].startswith("data:image/jpeg;base64,")
    assert body["input"]["garm_img"] == "https://api.replicate.com/v1/files/f1", "big images are uploaded first"
    assert ("GET", "/v1/predictions/p1") in seen, "a slow prediction is polled"


def test_replicate_errors_are_classified(caplog):
    vton = ReplicateTryOn("token", http=replicate_stub(["failed"], []))
    with pytest.raises(TryOnError) as e:
        vton.dress(b"p", "image/jpeg", Garment(b"g", "image/jpeg", "dresses", "dress"))
    # The user gets a plain sentence; the model's raw error goes to the log.
    assert str(e.value).startswith("The try-on model couldn't render") and not e.value.retryable
    assert "CUDA" in caplog.text

    def status(code):
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(code, json={})))

    for code, retryable in [(401, False), (402, False), (429, True), (503, True)]:
        with pytest.raises(TryOnError) as e:
            ReplicateTryOn("t", http=status(code)).version()
        assert e.value.retryable is retryable


def test_garment_photos_come_from_the_saved_dataset_images(tmp_path):
    from lookmate.catalog.service import ProductView
    from lookmate.tryon.garments import garment_for

    (tmp_path / "cache" / "images" / "polyvore").mkdir(parents=True)
    (tmp_path / "cache" / "images" / "polyvore" / "1_1.png").write_bytes(b"png")
    p = ProductView("pv-1_1", "Tibi knit dress", "Day Dresses", "dress", "black", "", "/catalog-images/polyvore/1_1.png", 300)
    g = garment_for(p, tmp_path)
    assert (g.image, g.media_type, g.region, g.description) == (b"png", "image/png", "dresses", "black Tibi knit dress")
    with pytest.raises(TryOnError):
        garment_for(ProductView("seed-1", "x", "Top", "top", "", "", "", 9), tmp_path)


def test_a_shop_photo_is_fetched_once_then_read_from_disk(tmp_path):
    from lookmate.catalog.service import ProductView
    from lookmate.tryon.garments import garment_for, prefetch

    tries = []
    jpg = b"\xff\xd8\xff\xe0jpg"

    def cdn(req):
        tries.append(req.extensions["timeout"]["read"])
        return httpx.Response(200, content=jpg, headers={"content-type": "image/jpeg"})

    p = ProductView("asos-2", "Wrap top", "Tops", "top", "white", "", "https://images.asos-media.com/products/y/1-2", 20)
    http = httpx.Client(transport=httpx.MockTransport(cdn))
    g = garment_for(p, tmp_path, http=http)
    assert g.image == jpg and tries[0] <= 10, "one try within the outfit's budget, not a long wait"
    assert garment_for(p, tmp_path, http=http).image == jpg and len(tries) == 1, "the second time comes from disk"

    q = ProductView("asos-3", "Midi skirt", "Skirts", "bottom", "white", "", "https://images.asos-media.com/products/z/1", 20)
    prefetch([q], tmp_path, http=http)
    assert garment_for(q, tmp_path, http=httpx.Client(transport=httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(AssertionError("should be cached"))))).image == jpg


def test_a_shop_photo_is_read_by_its_bytes_not_its_header(tmp_path, caplog):
    """A CDN can label a JPEG "image/jpg" or "binary/octet-stream", or send a format the models don't read."""
    import io as _io

    from PIL import Image

    from lookmate.tryon.garments import fetch_shop_photo

    png, gif = _io.BytesIO(), _io.BytesIO()
    Image.new("RGB", (4, 4), (200, 0, 0)).save(png, "PNG")
    Image.new("RGB", (4, 4), (200, 0, 0)).save(gif, "GIF")

    def serve(body, ctype="binary/octet-stream"):
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body, headers={"content-type": ctype})))

    assert fetch_shop_photo("https://cdn/a.png", tmp_path, serve(png.getvalue()))[1] == "image/png"
    data, media_type = fetch_shop_photo("https://cdn/b.gif", tmp_path, serve(gif.getvalue(), "image/gif"))
    assert media_type == "image/jpeg" and data[:3] == b"\xff\xd8\xff", "re-encoded for the try-on model"
    assert fetch_shop_photo("https://cdn/c", tmp_path, serve(b"<html>blocked</html>", "text/html"))[0] is None
    refused = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    assert fetch_shop_photo("https://cdn/d.jpg", tmp_path, refused)[0] is None
    assert "shop photo refused (403)" in caplog.text, "the server log says why a photo didn't load"


def test_a_photo_prefetched_while_the_try_on_waited_is_used(client, runtime, user, monkeypatch, tmp_path):
    """The fitting room's prefetch can land after the try-on stopped waiting for its own fetch."""
    import threading

    from lookmate import worker
    from lookmate.tryon.garments import _cached

    jpg = b"\xff\xd8\xff\xe0shop"
    release = threading.Event()
    seen = []

    class WholeOutfit:
        name, renders, whole_outfit = "nano-banana", True, True

        def dress_outfit(self, person, media_type, garments):
            seen.extend(garments)
            return b"dressed", "image/jpeg"

    dress_id = pick(runtime, "dress")[0]
    dress = runtime.catalog.products[dress_id]
    url = "https://images.asos-media.com/products/slow/1"
    monkeypatch.setitem(runtime.catalog.products, dress_id, replace(dress, image_url=url))

    def slow(p, data_dir):
        path = _cached(data_dir, url)  # meanwhile, the API's prefetch writes the photo to the shared cache
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image/jpeg\n" + jpg)
        release.wait(2)
        raise AssertionError("never reached in time")

    monkeypatch.setattr(runtime, "data_dir", tmp_path)
    monkeypatch.setattr(runtime, "tryon", WholeOutfit())
    monkeypatch.setattr(worker, "garment_for", slow)
    monkeypatch.setattr(worker, "GARMENT_BUDGET_S", 0.2)
    request_tryon(client, user["id"], [dress_id])
    run_next_job(runtime)
    release.set()
    assert seen and seen[0].image == jpg, "the cached photo, not a description"


def test_garment_photos_load_together_and_a_straggler_is_described(client, runtime, user, monkeypatch):
    import threading

    from lookmate import worker
    from lookmate.tryon.client import TRYON_STAGE

    class Outfit:
        name, renders, whole_outfit = "nano-banana", True, True

        def dress_outfit(self, person, media_type, garments):
            self.seen = garments
            return b"out", "image/jpeg"

    release = threading.Event()

    def slow_or_fast(p, data_dir):
        if p.category == "bottom":
            release.wait(5)  # stalls past the budget
        return Garment(b"img", "image/jpeg", "upper_body", p.name)

    model = Outfit()
    monkeypatch.setattr(runtime, "tryon", model)
    monkeypatch.setattr(worker, "garment_for", slow_or_fast)
    monkeypatch.setattr(worker, "GARMENT_BUDGET_S", 0.3)
    ids = pick(runtime, "top", "bottom")
    tid = request_tryon(client, user["id"], ids).json()["id"]
    t0 = time.monotonic()
    assert worker.process_tryon(runtime, tid) == "done"
    release.set()
    assert time.monotonic() - t0 < 2, "photos load in parallel within one budget"
    result = client.get(f"/api/tryons/{tid}").json()["result"]
    assert len(result["described_ids"]) == 1 and set(result["timings_ms"]) == {"fetch_ms", "model_ms"}
    assert [g.image for g in model.seen].count(None) == 1, "the stalled piece is drawn from its description"
    assert runtime.redis.get(TRYON_STAGE.format(tid)) is None


def test_shop_photo_falls_back_to_its_url_when_the_cdn_stalls():
    from lookmate.catalog.service import ProductView
    from lookmate.tryon.garments import garment_for

    def stall(req):
        raise httpx.ReadTimeout("timed out", request=req)

    url = "https://images.asos-media.com/products/x/1-4"
    p = ProductView("asos-1", "Satin cargo trousers in black", "Trousers", "bottom", "black", "", url, 28.6)
    g = garment_for(p, None, http=httpx.Client(transport=httpx.MockTransport(stall)))
    assert g.image is None and g.url == url and g.region == "lower_body"

    seen = []
    ReplicateTryOn("t", http=replicate_stub(["succeeded"], seen)).dress(b"p", "image/jpeg", g)
    body = next(s for s in seen if isinstance(s, dict))
    assert body["input"]["garm_img"] == url, "the try-on service fetches it instead"


def test_a_failed_tryon_can_be_tried_again(client, runtime, user, renderer, monkeypatch):
    ids = pick(runtime, "dress")
    monkeypatch.setattr(renderer, "dress", lambda *a: (_ for _ in ()).throw(TryOnError("model down")))
    first = request_tryon(client, user["id"], ids).json()["id"]
    assert run_next_job(runtime) == "failed"
    monkeypatch.undo()
    monkeypatch.setattr(runtime, "tryon", renderer)
    monkeypatch.setattr("lookmate.worker.garment_for",
                        lambda p, data_dir: Garment(b"img", "image/jpeg", "dresses", p.name))
    again = request_tryon(client, user["id"], ids)
    assert again.status_code == 202 and again.json()["id"] != first, "a failure isn't handed back as the answer"
    assert run_next_job(runtime) == "done"
    assert client.get(f"/api/tryons/{first}").status_code == 404


def test_tryons_have_their_own_queue_so_looks_never_wait_behind_them(client, runtime, user):
    request_tryon(client, user["id"], pick(runtime, "dress"))
    assert runtime.tryon_queue.depth()["ready"] == 1
    assert runtime.queue.depth()["ready"] == 0, "a slow render must not block looks and analyses"


def test_the_worker_serves_tryons_on_a_separate_thread(runtime, monkeypatch):
    import threading

    from lookmate import worker

    served = []
    shared = []
    monkeypatch.setattr(worker, "_serve", lambda rt, stop, trends, shares=(): (
        served.append((rt.queue, trends)), shared.extend(shares)))
    worker.run_forever(runtime, threading.Event())
    for t in threading.enumerate():
        if t.name == "tryon-worker":
            t.join(timeout=2)
    assert (runtime.queue, True) in served and (runtime.tryon_queue, False) in served
    assert [r.queue for r in shared] == [runtime.tryon_queue], "a reloaded catalog reaches the try-on thread too"


def test_an_old_tryon_on_the_shared_queue_is_handed_over(client, runtime, user, monkeypatch):
    import threading

    from lookmate import worker

    tryon_id = request_tryon(client, user["id"], pick(runtime, "dress")).json()["id"]
    runtime.tryon_queue.reserve(timeout_s=0.1)
    runtime.tryon_queue.ack(worker.tryon_job(tryon_id))
    runtime.queue.enqueue(worker.tryon_job(tryon_id))  # as an older deploy would have queued it
    stop = threading.Event()
    real_ack = runtime.queue.ack
    monkeypatch.setattr(runtime.queue, "ack", lambda job: (real_ack(job), stop.set()))
    worker._serve(runtime, stop, True)
    assert runtime.tryon_queue.depth()["ready"] == 1 and runtime.queue.depth()["ready"] == 0


def test_a_cold_replicate_model_is_cancelled_not_retried(monkeypatch):
    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr("lookmate.tryon.client.time.monotonic", lambda: next(clock))
    seen = []
    vton = ReplicateTryOn("token", timeout_s=240, http=replicate_stub(["starting"] * 50, seen))
    with pytest.raises(TryOnError) as err:
        vton.dress(b"person", "image/jpeg", Garment(b"g", "image/jpeg", "upper_body", "tee"))
    assert not err.value.retryable, "a retry would pay for a second prediction"
    assert "took too long" in str(err.value)
    assert ("POST", "/v1/predictions/p1/cancel") in seen


def fashn_stub(statuses, seen):
    state = iter(statuses)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.url.path == "/v1/run":
            seen.append(json.loads(req.content))
            return httpx.Response(200, json={"id": "f1", "error": None})
        if req.url.path == "/v1/status/f1":
            status = next(state)
            out = {"id": "f1", "status": status, "error": None}
            if status == "completed":
                out["output"] = ["https://cdn.fashn.ai/f1/out.jpg"]
            if status == "failed":
                out["error"] = {"name": "PoseError", "message": "Couldn't find a person"}
            return httpx.Response(200, json=out)
        if req.url.host == "cdn.fashn.ai":
            return httpx.Response(200, content=b"JPEGDATA", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fashn_client_renders_a_garment(monkeypatch):
    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    seen = []
    out = FashnTryOn("key", http=fashn_stub(["in_queue", "processing", "completed"], seen)).dress(
        b"person", "image/jpeg", Garment(None, "image/jpeg", "lower_body", "jeans", url="https://img/jeans.jpg"))
    assert out == (b"JPEGDATA", "image/jpeg")
    body = next(s for s in seen if isinstance(s, dict))
    assert body["model_name"] == "tryon-v1.6" and body["inputs"]["category"] == "bottoms"
    assert body["inputs"]["garment_image"] == "https://img/jeans.jpg"
    assert body["inputs"]["model_image"].startswith("data:image/jpeg;base64,")
    assert ("POST", "/v1/run", "Bearer key") in seen


def test_fashn_failure_is_explained(monkeypatch):
    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    with pytest.raises(TryOnError, match="Couldn't find a person"):
        FashnTryOn("key", http=fashn_stub(["failed"], [])).dress(
            b"p", "image/jpeg", Garment(b"g", "image/jpeg", "dresses", "dress"))


def test_fashn_is_preferred_when_configured():
    assert make_tryon("rep-token", fashn_key="fa-key").name == "fashn"
    assert make_tryon("rep-token").name == "nano-banana", "the Replicate default is warm and does whole outfits"
    assert make_tryon("rep-token", "cuuupid/idm-vton").name == "idm-vton"
    assert make_tryon("").name == "preview"


def test_nano_banana_dresses_the_whole_outfit_in_one_call(monkeypatch):
    from lookmate.tryon.client import NanoBananaTryOn

    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/v1/models/google/nano-banana/predictions":
            seen.append(json.loads(req.content))
            return httpx.Response(201, json={"id": "n1", "status": "processing",
                                             "urls": {"get": "https://api.replicate.com/v1/predictions/n1",
                                                      "cancel": "https://api.replicate.com/v1/predictions/n1/cancel"}})
        if req.url.path == "/v1/predictions/n1":
            return httpx.Response(200, json={"id": "n1", "status": "succeeded", "output": "https://replicate.delivery/n1.jpg"})
        if req.url.host == "replicate.delivery":
            return httpx.Response(200, content=b"JPG", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    nano = NanoBananaTryOn("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    out = nano.dress_outfit(b"me", "image/jpeg", [
        Garment(b"t", "image/jpeg", "upper_body", "white satin blouse"),
        Garment(None, "image/jpeg", "accessory", "black ballet flats", url="https://img/flats.jpg")])
    assert out == (b"JPG", "image/jpeg")
    body = next(s for s in seen if isinstance(s, dict))["input"]
    # A photo we couldn't fetch isn't passed on as a URL (the CDN stalls the model too): it's described in words.
    assert len(body["image_input"]) == 2 and not any(i.startswith("https://") for i in body["image_input"])
    assert "shown in the other images: white satin blouse, and" in body["prompt"]
    assert "no photo, as described: black ballet flats" in body["prompt"]
    assert not any(p == "/v1/models/google/nano-banana" for _, p in [x for x in seen if isinstance(x, tuple)]), \
        "official models need no version lookup"


def test_a_whole_outfit_model_renders_shoes_too(client, runtime, user, monkeypatch):
    calls = []

    class WholeOutfit:
        name, renders, whole_outfit = "nano-banana", True, True

        def dress_outfit(self, person, media_type, garments):
            calls.append([g.region for g in garments])
            return b"dressed", "image/jpeg"

    monkeypatch.setattr(runtime, "tryon", WholeOutfit())
    monkeypatch.setattr("lookmate.worker.garment_for",
                        lambda p, data_dir: Garment(None if p.category == "shoes" else b"img", "image/jpeg",
                                                    {"shoes": "accessory"}.get(p.category, "upper_body"), p.name))
    ids = pick(runtime, "top", "bottom", "shoes")
    tryon_id = request_tryon(client, user["id"], ids).json()["id"]
    assert run_next_job(runtime) == "done"
    assert len(calls) == 1 and "accessory" in calls[0], "one call, shoes included"
    result = client.get(f"/api/tryons/{tryon_id}").json()["result"]
    assert result["rendered_ids"] == ids
    assert result["described_ids"] == [ids[2]], "the shoes' photo wouldn't load, so they were drawn from words"


def test_a_try_on_photo_is_checked_before_it_is_used(client, runtime, user, monkeypatch):
    from lookmate.llm.schemas import PhotoCheck

    url = f"/api/users/{user['id']}/photo-check"
    ok = client.post(url, files={"photo": ("me.jpg", PHOTO, "image/jpeg")}).json()
    assert ok["good_for_tryon"] is True, "offline, the fake model accepts the photo"
    waist_up = PhotoCheck(framing="upper_body", good_for_colour=True, good_for_tryon=False,
                          tip="Step back so your knees are in the photo.")
    monkeypatch.setattr(runtime.llm, "check_photo", lambda data, media_type: waist_up)
    res = client.post(url, files={"photo": ("me.jpg", PHOTO, "image/jpeg")}).json()
    assert res["good_for_tryon"] is False and "knees" in res["tip"]
    assert client.post(url, files={"photo": ("me.pdf", b"%PDF", "application/pdf")}).status_code == 415


def test_a_busy_model_is_retried_but_a_refusal_is_not(monkeypatch):
    from lookmate.tryon.client import TRANSIENT

    assert TRANSIENT.search("Model is overloaded, please try again later")
    assert TRANSIENT.search("upstream returned 503")
    assert not TRANSIENT.search("CUDA out of memory") and not TRANSIENT.search("Input image flagged as sensitive")


def test_a_shop_photo_the_model_cant_read_is_described_instead():
    from lookmate.catalog.service import ProductView
    from lookmate.tryon.garments import BROWSER_HEADERS, garment_for

    assert "avif" not in BROWSER_HEADERS["Accept"], "never ask a CDN for AVIF"
    avif = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"avif", headers={"content-type": "image/avif"})))
    p = ProductView("asos-3", "Wrap top", "Tops", "top", "white", "", "https://images.asos-media.com/products/z/1-2", 20)
    g = garment_for(p, None, http=avif)
    assert g.image is None and g.description, "drawn from its description rather than sent as AVIF"


def test_fitting_room_pieces_are_prefetched_and_the_stage_is_shown(client, runtime, user, renderer, monkeypatch):
    from lookmate.tryon.client import TRYON_STAGE

    warmed = []
    monkeypatch.setattr("lookmate.api.tryon.prefetch", lambda products, data_dir: warmed.extend(p.id for p in products))
    ids = pick(runtime, "top")
    assert client.post("/api/tryon/prefetch", json={"product_ids": ids + ["nope"]}).json() == {"queued": 1}
    assert warmed == ids

    tid = request_tryon(client, user["id"], ids).json()["id"]
    from lookmate.db import SessionLocal
    from lookmate.models import TryOn
    with SessionLocal() as s:
        s.get(TryOn, tid).status = "processing"
        s.commit()
    runtime.redis.set(TRYON_STAGE.format(tid), "fetching")
    assert client.get(f"/api/tryons/{tid}").json()["stage"] == "fetching"
