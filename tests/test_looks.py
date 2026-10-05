"""End-to-end look pipeline with the offline FakeVision model: upload -> queue -> worker -> results."""

import pytest

from lookmate.llm.client import FakeVision, LLMError, LLMTransientError
from lookmate.worker import process_look

PNG = b"\x89PNG\r\n\x1a\n" + b"look-one"


def upload(client, user_id, data=PNG, ctype="image/png"):
    return client.post("/api/looks", data={"user_id": user_id}, files={"image": ("look.png", data, ctype)})


def run_next_job(runtime):
    job = runtime.queue.reserve(timeout_s=0.1)
    assert job is not None, "expected a queued job"
    return process_look(runtime, int(job))


def test_upload_is_processed_into_per_item_lookalikes(client, runtime, user):
    res = upload(client, user["id"])
    assert res.status_code == 202 and res.json()["status"] == "queued"

    assert run_next_job(runtime) == "done"
    look = client.get(f"/api/looks/{res.json()['id']}").json()
    assert look["status"] == "done"

    sections = look["result"]["sections"]
    assert sections, "every detected item gets a section"
    assert any(s["picks"] for s in sections)
    for s in sections:
        assert s["picks"] or s["note"], f"{s['item']['name']}: no picks and no explanation"
        assert all(p["category"] == s["item"]["category"] for p in s["picks"])
        assert all(p["reasons"] is not None for p in s["picks"])
    all_ids = [p["id"] for s in sections for p in s["picks"]]
    assert len(all_ids) == len(set(all_ids)), "a product is never recommended for two items"


def test_photo_is_deleted_after_processing(client, runtime, user):
    from lookmate.db import SessionLocal
    from lookmate.models import Look

    look_id = upload(client, user["id"]).json()["id"]
    run_next_job(runtime)
    with SessionLocal() as s:
        assert s.get(Look, look_id).image is None


def test_same_photo_is_deduplicated(client, runtime, user):
    first = upload(client, user["id"])
    second = upload(client, user["id"])
    assert second.status_code == 200 and second.json()["deduplicated"] is True
    assert second.json()["id"] == first.json()["id"]
    assert runtime.queue.depth()["ready"] == 1


def test_upload_validation(client, user):
    assert upload(client, user["id"], ctype="application/pdf").status_code == 415
    assert upload(client, user["id"], data=b"").status_code == 400
    assert upload(client, 424242).status_code == 404


def test_duplicate_delivery_is_harmless(client, runtime, user):
    look_id = upload(client, user["id"]).json()["id"]
    assert run_next_job(runtime) == "done"
    runtime.queue.enqueue(look_id)  # simulate at-least-once redelivery
    assert run_next_job(runtime) == "done"
    assert runtime.queue.depth() == {"ready": 0, "processing": 0, "delayed": 0}


class FlakyVision(FakeVision):
    def __init__(self, failures):
        self.failures = failures

    def analyze_look(self, image, media_type):
        if self.failures:
            self.failures -= 1
            raise LLMTransientError("overloaded")
        return super().analyze_look(image, media_type)


def test_transient_llm_errors_are_retried_with_backoff(client, runtime, user, monkeypatch):
    monkeypatch.setattr(runtime, "llm", FlakyVision(failures=1))
    look_id = upload(client, user["id"]).json()["id"]
    assert run_next_job(runtime) == "queued"
    assert runtime.queue.depth()["delayed"] == 1

    assert runtime.queue.promote_due(now=10**12) == 1
    assert run_next_job(runtime) == "done"
    assert client.get(f"/api/looks/{look_id}").json()["attempts"] == 2


def test_gives_up_after_max_attempts(client, runtime, user, monkeypatch):
    monkeypatch.setattr(runtime, "llm", FlakyVision(failures=99))
    look_id = upload(client, user["id"]).json()["id"]
    statuses = []
    for _ in range(3):
        statuses.append(run_next_job(runtime))
        runtime.queue.promote_due(now=10**12)
    assert statuses == ["queued", "queued", "failed"]
    assert client.get(f"/api/looks/{look_id}").json()["status"] == "failed"


@pytest.mark.parametrize("error", [LLMError("bad request")])
def test_permanent_errors_fail_immediately(client, runtime, user, monkeypatch, error):
    class Broken(FakeVision):
        def analyze_look(self, image, media_type):
            raise error

    monkeypatch.setattr(runtime, "llm", Broken())
    upload(client, user["id"])
    assert run_next_job(runtime) == "failed"


def test_recover_requeues_jobs_held_by_a_crashed_worker(client, runtime, user):
    upload(client, user["id"])
    runtime.queue.reserve(timeout_s=0.1)  # worker takes the job, then "crashes"
    assert runtime.queue.depth()["processing"] == 1
    assert runtime.queue.recover() == 1
    assert run_next_job(runtime) == "done"
