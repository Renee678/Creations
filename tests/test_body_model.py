""""My model": one full-body photo -> a standing base figure the fitting room dresses."""

import json
from dataclasses import replace

import httpx
import pytest

from lookmate.tryon.client import NanoBananaTryOn, TryOnError, make_model_maker, model_prompt
from lookmate.worker import process_job

PHOTO = b"\xff\xd8\xff\xe0" + b"full-body-photo"


class FakeModelMaker:
    """Stands in for Nano Banana: tags the photo, and remembers the size hints it was given."""

    name = "fake-nano-banana"

    def __init__(self):
        self.calls = []

    def make_model(self, person, media_type, height_cm=None, weight_kg=None):
        self.calls.append((person, height_cm, weight_kg))
        return b"MODEL:" + person, "image/jpeg"


@pytest.fixture
def maker(runtime, monkeypatch):
    fake = FakeModelMaker()
    monkeypatch.setattr(runtime, "model_maker", fake)
    return fake


def create(client, user_id, photo=PHOTO, original=False):
    return client.post(f"/api/users/{user_id}/model", data={"original": str(original).lower()},
                       files={"photo": ("me.jpg", photo, "image/jpeg")})


def run_next_job(runtime):
    job = runtime.tryon_queue.reserve(timeout_s=0.1)
    assert job is not None, "expected a queued job"
    return process_job(replace(runtime, queue=runtime.tryon_queue), job)


def test_the_prompt_carries_height_weight_and_no_slimming():
    prompt = model_prompt(165, 55.5)
    assert "165 cm tall" in prompt and "55.5 kg" in prompt
    assert "Do not slim, reshape or beautify the body" in prompt
    for must in ("same face, skin tone, hair", "real body shape and proportions", "facing the camera",
                 "arms relaxed at the sides", "head to toe", "white tank top", "denim shorts", "white sneakers",
                 "light-grey studio"):
        assert must in prompt, must
    assert "cm tall" not in model_prompt(), "no hints without a profile size"


def test_nano_banana_draws_the_model_in_one_call(monkeypatch):
    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/models/google/nano-banana/predictions":
            sent.append(json.loads(req.content))
            return httpx.Response(201, json={"id": "m1", "status": "succeeded", "output": "https://replicate.delivery/m1.jpg",
                                             "urls": {"get": "", "cancel": ""}})
        if req.url.host == "replicate.delivery":
            return httpx.Response(200, content=b"JPG", headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    nano = NanoBananaTryOn("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert nano.make_model(b"me", "image/jpeg", 170, 62) == (b"JPG", "image/jpeg")
    body = sent[0]["input"]
    assert len(body["image_input"]) == 1 and "170 cm tall and 62 kg" in body["prompt"]
    assert "Do not slim" in body["prompt"]


def test_only_a_replicate_token_makes_a_model_maker():
    assert make_model_maker("") is None
    assert make_model_maker("rep-token").name == "nano-banana"


def test_without_a_token_the_photo_is_saved_as_it_is(client, runtime, user):
    assert runtime.model_maker is None
    res = create(client, user["id"])
    out = res.json()
    assert res.status_code == 201 and out["status"] == "done"
    assert out["generated"] is False and "REPLICATE_API_TOKEN" in out["note"]
    assert client.get(out["image_url"]).content == PHOTO
    assert runtime.tryon_queue.depth()["ready"] == 0, "nothing to queue"


def test_a_model_is_generated_by_a_queued_job(client, runtime, user, maker):
    from lookmate.db import SessionLocal
    from lookmate.models import BodyModel

    res = create(client, user["id"])
    assert res.status_code == 202 and len(res.json()["id"]) == 32, "ids are unguessable tokens"
    mid = res.json()["id"]
    assert run_next_job(runtime) == "done"

    out = client.get(f"/api/body-models/{mid}").json()
    assert out["generated"] is True and out["saved"] is False
    assert "model_ms" in out["timings_ms"]
    assert maker.calls == [(PHOTO, 165, 55)], "the profile's height and weight go in as hints"
    assert client.get(out["image_url"]).content == b"MODEL:" + PHOTO
    with SessionLocal() as s:
        assert s.get(BodyModel, mid).photo is None, "the uploaded original isn't kept"


def test_a_repeated_delivery_does_not_draw_twice(client, runtime, user, maker):
    mid = create(client, user["id"]).json()["id"]
    run_next_job(runtime)
    process_job(replace(runtime, queue=runtime.tryon_queue), f"model:{mid}")
    assert len(maker.calls) == 1


def test_a_busy_model_is_retried_then_done(client, runtime, user, maker, monkeypatch):
    real = maker.make_model
    calls = iter([TryOnError("busy", retryable=True)])

    def flaky(*a):
        err = next(calls, None)
        if err:
            raise err
        return real(*a)

    monkeypatch.setattr(maker, "make_model", flaky)
    monkeypatch.setattr("lookmate.worker.BACKOFF_BASE_S", 0)
    mid = create(client, user["id"]).json()["id"]
    assert run_next_job(runtime) == "queued"
    runtime.tryon_queue.promote_due()
    assert run_next_job(runtime) == "done"
    assert client.get(f"/api/body-models/{mid}").json()["attempts"] == 2


def test_use_my_original_photo_skips_the_model(client, runtime, user, maker):
    out = create(client, user["id"], original=True).json()
    assert out["status"] == "done" and out["generated"] is False and maker.calls == []


def test_save_keeps_one_model_and_delete_removes_it(client, runtime, user):
    assert client.get(f"/api/users/{user['id']}/model").json() == {"model": None}
    first = create(client, user["id"], b"\xff\xd8first").json()
    client.post(f"/api/body-models/{first['id']}/save")
    second = create(client, user["id"], b"\xff\xd8second").json()
    assert client.get(f"/api/users/{user['id']}/model").json()["model"]["id"] == first["id"], \
        "a new draft doesn't replace the saved model until it is saved"

    saved = client.post(f"/api/body-models/{second['id']}/save").json()
    assert saved["saved"] is True
    assert client.get(f"/api/body-models/{first['id']}").status_code == 404, "the old model goes"
    assert client.get(f"/api/users/{user['id']}/model").json()["model"]["id"] == second["id"]

    assert client.delete(f"/api/users/{user['id']}/model").status_code == 204
    assert client.get(f"/api/users/{user['id']}/model").json() == {"model": None}
    assert client.get(f"/api/body-models/{second['id']}/image").status_code == 404


def test_the_fitting_room_prefers_the_saved_model(client, runtime, user):
    from tests.test_tryon import pick

    model = create(client, user["id"], b"\xff\xd8my-model").json()
    client.post(f"/api/body-models/{model['id']}/save")
    ids = pick(runtime, "dress")
    # No photo at all: the saved model is the person. With a photo, the model still wins.
    for files in ({}, {"photo": ("me.jpg", PHOTO, "image/jpeg")}):
        res = client.post(f"/api/users/{user['id']}/tryons", data={"product_ids": ",".join(ids)}, files=files)
        assert res.status_code in (200, 202)
    job = runtime.tryon_queue.reserve(timeout_s=0.1)
    process_job(replace(runtime, queue=runtime.tryon_queue), job)
    tryon = client.get(f"/api/tryons/{res.json()['id']}").json()
    assert client.get(tryon["image_url"]).content == b"\xff\xd8my-model"


def test_without_a_model_the_try_on_still_needs_a_photo(client, runtime, user):
    from tests.test_tryon import pick

    res = client.post(f"/api/users/{user['id']}/tryons", data={"product_ids": ",".join(pick(runtime, "dress"))})
    assert res.status_code == 400 and "My model" in res.json()["detail"]
