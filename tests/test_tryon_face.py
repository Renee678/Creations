"""Try-on on "My model" keeps the user's face: a face close-up goes in, and the head is pasted back."""

import io
import json
from dataclasses import replace

import httpx
from PIL import Image, ImageDraw

from lookmate.tryon.client import Garment, NanoBananaTryOn
from lookmate.tryon.face import face_crop, head_box, paste_head
from lookmate.worker import process_job

GREY, HAIR, SHIRT = (225, 225, 228), (40, 30, 25), (250, 250, 250)


def person(size=(300, 400), head=HAIR, dx=0) -> bytes:
    """A stand-in My model: a dark-haired head and a body on a light-grey studio backdrop."""
    img = Image.new("RGB", size, GREY)
    d = ImageDraw.Draw(img)
    w, h = size
    cx = w // 2 + dx
    d.ellipse((cx - w * 0.09, h * 0.04, cx + w * 0.09, h * 0.19), fill=head)
    d.rectangle((cx - w * 0.16, h * 0.2, cx + w * 0.16, h * 0.6), fill=SHIRT if head == HAIR else (200, 40, 40))
    d.rectangle((cx - w * 0.12, h * 0.6, cx + w * 0.12, h * 0.95), fill=(90, 120, 170))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def pixel(data: bytes, xy) -> tuple:
    return Image.open(io.BytesIO(data)).convert("RGB").getpixel(xy)


def test_the_head_is_found_on_a_studio_backdrop():
    l, t, r, b = head_box(Image.open(io.BytesIO(person())).convert("RGB"))
    assert 120 <= (l + r) / 2 <= 180, "centred on the head"
    assert t <= 400 * 0.05 and 400 * 0.17 <= b <= 400 * 0.3, "from the top of the hair to about the chin"


def test_the_face_close_up_is_upscaled_and_shows_the_hair():
    crop, media_type = face_crop(person())
    img = Image.open(io.BytesIO(crop))
    assert media_type == "image/jpeg" and max(img.size) == 768
    assert pixel(crop, (img.width // 2, img.height // 3)) < (90, 90, 90), "the dark hair is in the close-up"
    assert face_crop(b"not an image") is None


def test_the_head_is_pasted_back_only_when_the_frames_line_up():
    model = person()
    render = person(head=(200, 160, 60))  # same pose, the head redrawn as someone else
    out, _ = paste_head(model, render)
    assert pixel(out, (150, 45)) < (90, 90, 90), "My model's own head is back"
    assert pixel(out, (150, 300)) == pixel(render, (150, 300)), "the outfit is untouched"
    assert paste_head(model, person(size=(400, 400))) is None, "a different frame: skipped"
    assert paste_head(model, person(head=(200, 160, 60), dx=60)) is None, "the person moved: skipped"
    big, _ = paste_head(model, person(size=(600, 800), head=(200, 160, 60)))
    assert pixel(big, (300, 90)) < (90, 90, 90), "the same frame at a larger size is scaled"


def test_nano_banana_gets_the_face_as_image_two(monkeypatch):
    monkeypatch.setattr("lookmate.tryon.client.time.sleep", lambda s: None)
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/predictions"):
            sent.append(json.loads(req.content)["input"])
            return httpx.Response(201, json={"id": "n1", "status": "succeeded", "output": "https://replicate.delivery/n1.jpg",
                                             "urls": {"get": "", "cancel": ""}})
        return httpx.Response(200, content=b"JPG", headers={"content-type": "image/jpeg"})

    nano = NanoBananaTryOn("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    top = Garment(b"t", "image/jpeg", "upper_body", "white satin blouse")
    nano.dress_outfit(b"me", "image/jpeg", [top], face=(b"face", "image/jpeg"))
    nano.dress_outfit(b"me", "image/jpeg", [top])
    with_face, without = sent
    assert len(with_face["image_input"]) == 3 and len(without["image_input"]) == 2
    assert "Image 2 is a close-up of this same person's face and hair" in with_face["prompt"]
    assert "same hairstyle and hair length" in with_face["prompt"]
    assert "shown in images 3 onward: white satin blouse" in with_face["prompt"]
    assert "Image 2" not in without["prompt"] and "shown in the other images" in without["prompt"]


class WholeOutfit:
    """Stands in for Nano Banana: returns a render with the head redrawn as someone else."""

    name, renders, whole_outfit, takes_face = "nano-banana", True, True, True

    def __init__(self, render):
        self.render, self.faces = render, []

    def dress_outfit(self, person, media_type, garments, face=None):
        self.faces.append(face)
        return self.render, "image/png"


def _setup(client, runtime, user, monkeypatch, tryon, with_model=True):
    from tests.test_body_model import create

    monkeypatch.setattr(runtime, "tryon", tryon)
    monkeypatch.setattr("lookmate.worker.garment_for",
                        lambda p, data_dir: Garment(b"img", "image/jpeg", "upper_body", p.name))
    if with_model:
        model = create(client, user["id"], person()).json()
        client.post(f"/api/body-models/{model['id']}/save")


def _try_on(client, runtime, user, ids, photo=None):
    files = {"photo": ("me.png", photo, "image/png")} if photo else {}
    tryon = client.post(f"/api/users/{user['id']}/tryons", data={"product_ids": ",".join(ids)}, files=files).json()
    job = runtime.tryon_queue.reserve(timeout_s=0.1)
    process_job(replace(runtime, queue=runtime.tryon_queue), job)
    return client.get(f"/api/tryons/{tryon['id']}").json()


def test_a_try_on_on_my_model_sends_the_face_and_pastes_the_head(client, runtime, user, monkeypatch):
    from tests.test_tryon import pick

    fake = WholeOutfit(person(head=(200, 160, 60)))
    _setup(client, runtime, user, monkeypatch, fake)
    out = _try_on(client, runtime, user, pick(runtime, "top", "bottom"))
    assert fake.faces[0] is not None and fake.faces[0][1] == "image/jpeg", "the face close-up went in"
    assert out["result"]["face_reference"] is True and out["result"]["head_pasted"] is True
    assert "paste_ms" in out["result"]["timings_ms"]
    assert pixel(client.get(out["image_url"]).content, (150, 45)) < (90, 90, 90), "her own head and hair"


def test_no_paste_under_a_hat(client, runtime, user, monkeypatch):
    from tests.test_tryon import pick

    hat = next(p.id for p in runtime.catalog.products.values() if "hat" in p.name.lower() and p.category == "accessory")
    fake = WholeOutfit(person(head=(200, 160, 60)))
    _setup(client, runtime, user, monkeypatch, fake)
    out = _try_on(client, runtime, user, pick(runtime, "top", "bottom") + [hat])
    assert out["result"]["face_reference"] is True and "head_pasted" not in out["result"]


def test_a_photo_try_on_has_no_face_reference_or_paste(client, runtime, user, monkeypatch):
    from tests.test_tryon import pick

    fake = WholeOutfit(person(head=(200, 160, 60)))
    _setup(client, runtime, user, monkeypatch, fake, with_model=False)
    out = _try_on(client, runtime, user, pick(runtime, "top", "bottom"), photo=person())
    assert fake.faces == [None]
    assert "face_reference" not in out["result"] and "head_pasted" not in out["result"]


def test_a_try_on_model_without_face_support_is_called_as_before(client, runtime, user, monkeypatch):
    from tests.test_tryon import pick

    class Plain:  # e.g. the other whole-outfit fakes: no face parameter
        name, renders, whole_outfit = "fake", True, True

        def dress_outfit(self, person, media_type, garments):
            return person, media_type

    _setup(client, runtime, user, monkeypatch, Plain())
    out = _try_on(client, runtime, user, pick(runtime, "top", "bottom"))
    assert out["status"] == "done" and "face_reference" not in out["result"]
