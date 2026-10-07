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
    assert "165 cm tall and 55.5 kg" in prompt
    assert "neither slimmer nor heavier" in prompt and "a slim build (BMI 20)" in prompt
    for must in ("same face, skin tone and hair", "the numbers decide the body's size", "facing the camera",
                 "arms relaxed at the sides", "head to toe", "white tank top", "denim shorts", "white sneakers",
                 "light-grey studio"):
        assert must in prompt, must
    assert "cm tall" not in model_prompt(), "no hints without a profile size"


def test_my_model_takes_its_size_from_height_and_weight_not_from_baggy_clothes():
    """Renee (2026-10-07): 166 cm and 55 kg, photographed in a baggy hoodie, came out heavier than she is. With
    the profile's height and weight, the numbers decide the body's size; the photo gives face, hair, proportions."""
    from lookmate.tryon.client import build_word

    prompt = model_prompt(166, 55)
    assert "166 cm tall and 55 kg, a slim build (BMI 20). Draw the typical body of a person of" in prompt
    assert "the numbers decide the body's size, whatever the photo seems to show" in prompt
    assert "wide-leg" in prompt and "never draw them wider than the numbers say" in prompt and "Do not slim" not in prompt
    assert [build_word(166, w) for w in (48, 55, 65, 75, 90)] == [
        "a very slim build (BMI 17)", "a slim build (BMI 20)", "an average build (BMI 24)",
        "a curvy, a little fuller build (BMI 27)", "a plus-size build (BMI 33)"]
    alone = model_prompt(166)
    assert "real body shape and proportions (166 cm tall)" in alone and "BMI" not in alone, "height alone: the photo's body"
    assert "never from the outline of the clothes" in model_prompt()


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
    assert "neither slimmer nor heavier" in body["prompt"]


def test_only_a_replicate_token_makes_a_model_maker():
    assert make_model_maker("") is None
    assert make_model_maker("rep-token").name == "google/nano-banana-pro", "Pro draws My model by default"


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


def test_a_try_on_on_my_model_is_the_outfit_pages_on_me_photo(client, runtime, user):
    """Feedback #34: the Daily Look ("On me") hero is the latest try-on of the outfit, made on My model."""
    from tests.test_tryon import pick

    model = create(client, user["id"], b"\xff\xd8my-model").json()
    client.post(f"/api/body-models/{model['id']}/save")
    ids = pick(runtime, "top", "bottom")
    tryon = client.post(f"/api/users/{user['id']}/tryons", data={"product_ids": ",".join(ids)}).json()
    run_next_job(runtime)
    outfit = client.post(f"/api/users/{user['id']}/outfits", json={"title": "Mine", "product_ids": ids,
                                                                   "style_id": "old_money", "source": "lookbook"}).json()
    page = client.get(f"/api/users/{user['id']}/outfits").json()["outfits"][0]
    assert page["id"] == outfit["id"] and page["tryon_image"] == f"/api/tryons/{tryon['id']}/image"


def _replicate(handler_for_model):
    """A Replicate stand-in: `handler_for_model(model, body)` answers each prediction request."""
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/predictions"):
            model = req.url.path.removeprefix("/v1/models/").removesuffix("/predictions")
            body = json.loads(req.content)["input"]
            sent.append((model, body))
            return handler_for_model(model, body)
        return httpx.Response(200, content=b"JPG", headers={"content-type": "image/jpeg"})

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def _done(model, body):
    return httpx.Response(201, json={"id": "m", "status": "succeeded", "output": "https://replicate.delivery/m.jpg",
                                     "urls": {"get": "", "cancel": ""}})


def test_my_model_is_drawn_by_nano_banana_pro_and_falls_back_on_error(monkeypatch):
    from lookmate.tryon.client import ModelMaker

    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    http, sent = _replicate(_done)
    assert ModelMaker("t", http=http).make_model(b"me", "image/jpeg", 165, 55) == (b"JPG", "image/jpeg",
                                                                                  "google/nano-banana-pro")
    assert [m for m, _ in sent] == ["google/nano-banana-pro"]

    http, sent = _replicate(lambda model, body: httpx.Response(422, json={"detail": "bad"}) if model.endswith("pro")
                            else _done(model, body))
    out = ModelMaker("t", http=http).make_model(b"me", "image/jpeg", 165, 55)
    assert out[2] == "google/nano-banana" and [m for m, _ in sent] == ["google/nano-banana-pro", "google/nano-banana"]

    http, _ = _replicate(lambda model, body: httpx.Response(422, json={}))
    with pytest.raises(TryOnError):
        ModelMaker("t", http=http).make_model(b"me", "image/jpeg")


def test_the_face_close_up_goes_in_only_when_uploaded(monkeypatch):
    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    http, sent = _replicate(_done)
    nano = NanoBananaTryOn("t", "google/nano-banana-pro", http=http)
    nano.make_model(b"me", "image/jpeg", 165, 55, face=(b"face", "image/png"))
    nano.make_model(b"me", "image/jpeg", 165, 55)
    (_, with_face), (_, without) = sent
    assert len(with_face["image_input"]) == 2 and with_face["image_input"][1].startswith("data:image/png")
    assert "Image 2 is a close-up of the same person's face" in with_face["prompt"]
    assert "Do not beautify or change it." in with_face["prompt"]
    assert len(without["image_input"]) == 1 and "Image 2" not in without["prompt"]


def test_both_uploads_go_to_the_model_maker_and_are_deleted_after(client, runtime, user, monkeypatch):
    from lookmate.db import SessionLocal
    from lookmate.models import BodyModel

    faces = []

    class WithFace(FakeModelMaker):
        def make_model(self, person, media_type, height_cm=None, weight_kg=None, face=None):
            faces.append(face)
            return b"MODEL:" + person, "image/jpeg", "google/nano-banana-pro"

    monkeypatch.setattr(runtime, "model_maker", WithFace())
    res = client.post(f"/api/users/{user['id']}/model", data={"original": "false"},
                      files={"photo": ("me.jpg", PHOTO, "image/jpeg"), "face": ("face.png", b"\x89PNGface", "image/png")})
    mid = res.json()["id"]
    run_next_job(runtime)
    assert faces == [(b"\x89PNGface", "image/png")]
    with SessionLocal() as s:
        rec = s.get(BodyModel, mid)
        assert rec.photo is None and rec.face_photo is None, "neither upload is kept"
        assert rec.result["model"] == "google/nano-banana-pro" and rec.result["face_photo"] is True


def test_without_a_face_upload_the_model_maker_is_called_as_before(client, runtime, user, maker):
    mid = create(client, user["id"]).json()["id"]  # FakeModelMaker takes no face argument
    assert run_next_job(runtime) == "done"
    assert client.get(f"/api/body-models/{mid}").json()["generated"] is True
