"""Virtual try-on: request -> queue -> worker -> image, with the collage preview when no model is set."""

import json

import httpx
import pytest

from lookmate.tryon.client import Garment, PreviewTryOn, ReplicateTryOn, TryOnError, plan_steps
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
    job = runtime.queue.reserve(timeout_s=0.1)
    assert job is not None, "expected a queued job"
    return process_job(runtime, job)


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
    assert runtime.queue.depth()["ready"] == 1


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
    runtime.queue.promote_due(now=1e12)
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
            out = {"id": "p1", "status": status, "urls": {"get": "https://api.replicate.com/v1/predictions/p1"}}
            if status == "succeeded":
                out["output"] = "https://replicate.delivery/out.png"
            if status == "failed":
                out["error"] = "CUDA out of memory"
            return httpx.Response(201 if req.method == "POST" else 200, json=out)
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


def test_replicate_errors_are_classified():
    vton = ReplicateTryOn("token", http=replicate_stub(["failed"], []))
    with pytest.raises(TryOnError) as e:
        vton.dress(b"p", "image/jpeg", Garment(b"g", "image/jpeg", "dresses", "dress"))
    assert "CUDA" in str(e.value) and not e.value.retryable

    def status(code):
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(code, json={})))

    for code, retryable in [(401, False), (402, False), (429, True), (503, True)]:
        with pytest.raises(TryOnError) as e:
            ReplicateTryOn("t", http=status(code)).version()
        assert e.value.retryable is retryable
