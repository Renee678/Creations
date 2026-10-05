"""Background worker: turns queued uploads into look-alike results, personal analyses and try-ons.

Run with `python -m lookmate.worker`. Each job is handled idempotently, since
the queue delivers at least once.
"""

import base64
import logging
import signal
import threading
import time

from .config import get_settings
from .db import SessionLocal
from .llm.client import LLMError
from .models import Look, PersonalAnalysis, TryOn, User, utcnow
from .runtime import Runtime, build_runtime
from .services.dupes import find_dupes
from .services.style_memory import record_look, user_context
from .services.trends import refresh_if_due
from .services.vocab import STYLES
from .tryon.client import TryOnError, plan_steps
from .tryon.garments import garment_for

log = logging.getLogger("lookmate.worker")

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0
TREND_CHECK_EVERY_S = 600


ANALYSIS_PREFIX = "analysis:"
TRYON_PREFIX = "tryon:"


def analysis_job(analysis_id: int) -> str:
    """Queue key for a personal analysis; looks use their bare id."""
    return f"{ANALYSIS_PREFIX}{analysis_id}"


def tryon_job(tryon_id: str) -> str:
    return f"{TRYON_PREFIX}{tryon_id}"


def process_look(rt: Runtime, look_id: int) -> str:
    """Process one job. Returns the look's resulting status."""
    with SessionLocal() as session:
        look = session.get(Look, look_id)
        if look is None:
            rt.queue.ack(look_id)
            return "missing"
        if look.status in ("done", "failed"):  # duplicate delivery: already handled
            rt.queue.ack(look_id)
            return look.status

        look.status, look.attempts = "processing", look.attempts + 1
        session.commit()
        started = time.monotonic()
        analysis = _call_llm(rt, session, look, look_id, lambda: rt.llm.analyze_look(look.image, look.media_type))
        if analysis is None:
            return look.status

        if not analysis.is_outfit or not analysis.items:
            _finish(session, look, "failed", error="No clothing found in this image. Try another outfit photo?")
            rt.queue.ack(look_id)
            return "failed"

        user = session.get(User, look.user_id)
        result = find_dupes(analysis, rt.catalog, user_context(session, user))
        result["model"] = rt.llm.name
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        record_look(session, user.id, analysis)
        _finish(session, look, "done", result=result)
        rt.queue.ack(look_id)
        log.info("look %s done in %sms", look_id, result["latency_ms"])
        return "done"


def process_analysis(rt: Runtime, analysis_id: int) -> str:
    """Personal colour/face analysis from the user's photos. Same retry and idempotency rules as looks."""
    job = analysis_job(analysis_id)
    with SessionLocal() as session:
        rec = session.get(PersonalAnalysis, analysis_id)
        if rec is None:
            rt.queue.ack(job)
            return "missing"
        if rec.status in ("done", "failed"):
            rt.queue.ack(job)
            return rec.status

        rec.status, rec.attempts = "processing", rec.attempts + 1
        session.commit()
        photos = [(base64.b64decode(p["data"]), p["media_type"]) for p in rec.photos or []]
        analysis = _call_llm(rt, session, rec, job, lambda: rt.llm.analyze_person(photos))
        if analysis is None:
            return rec.status

        if not analysis.usable:
            _finish(session, rec, "failed", error="We couldn't see your face clearly. Try a well-lit selfie without filters.")
            rt.queue.ack(job)
            return "failed"

        result = analysis.model_dump()
        result["style_tags"] = [t for t in analysis.style_tags if t in STYLES]
        result["model"] = rt.llm.name
        _finish(session, rec, "done", result=result)
        rt.queue.ack(job)
        log.info("analysis %s done: %s", analysis_id, analysis.season_detail)
        return "done"


def process_tryon(rt: Runtime, tryon_id: str) -> str:
    """Dress the user's photo in the outfit, one garment per model call (see tryon.client)."""
    job = tryon_job(tryon_id)
    with SessionLocal() as session:
        rec = session.get(TryOn, tryon_id)
        if rec is None:
            rt.queue.ack(job)
            return "missing"
        if rec.status in ("done", "failed"):
            rt.queue.ack(job)
            return rec.status

        rec.status, rec.attempts = "processing", rec.attempts + 1
        session.commit()
        started = time.monotonic()
        pieces = [rt.catalog.products[i] for i in rec.product_ids if i in rt.catalog.products]
        steps = plan_steps([{"category": p.category, "product": p} for p in pieces])

        def render():
            image, media_type, done = rec.photo, rec.media_type, []
            if rt.tryon.renders:
                for step in steps:
                    try:
                        garment = garment_for(step["product"], rt.data_dir)
                    except TryOnError as e:
                        if e.retryable:
                            raise
                        log.warning("try-on %s: skipping %s (%s)", tryon_id, step["product"].id, e)
                        continue  # pinned beside the picture instead
                    image, media_type = rt.tryon.dress(image, media_type, garment)
                    done.append(step["product"].id)
                if not done:
                    raise TryOnError("None of these pieces has a photo the try-on model can use.")
            return image, media_type, done

        out = _call_llm(rt, session, rec, job, render)
        if out is None:
            return rec.status
        rec.result_image, rec.result_media_type, done = out
        rendered = bool(done)
        _finish(session, rec, "done", result={
            "rendered": rendered,
            "rendered_ids": done,
            "model": rt.tryon.name,
            "latency_ms": round((time.monotonic() - started) * 1000),
        })
        rt.queue.ack(job)
        log.info("try-on %s done (%s, %d steps)", tryon_id, rt.tryon.name, len(done))
        return "done"


def _call_llm(rt: Runtime, session, rec, job, call):
    """Run the model call; on failure schedule a retry or fail the record, and return None."""
    try:
        return call()
    except (LLMError, TryOnError) as e:
        if e.retryable and rec.attempts < MAX_ATTEMPTS:
            delay = BACKOFF_BASE_S * 2 ** (rec.attempts - 1)
            log.warning("job %s attempt %s failed (%s); retrying in %.0fs", job, rec.attempts, e, delay)
            rec.status, rec.error = "queued", str(e)
            session.commit()
            rt.queue.retry_later(job, delay)
            return None
        log.error("job %s failed permanently: %s", job, e)
        _finish(session, rec, "failed", error=str(e))
        rt.queue.ack(job)
        return None


def _finish(session, rec, status: str, result: dict | None = None, error: str | None = None) -> None:
    rec.status, rec.result, rec.error = status, result, error
    rec.model = (result or {}).get("model")
    # Drop the photos as soon as we're done with them.
    if isinstance(rec, Look):
        rec.image = None
    elif isinstance(rec, TryOn):
        rec.photo = None
    else:
        rec.photos = None
    rec.finished_at = utcnow()
    session.commit()


def process_job(rt: Runtime, job: str) -> str:
    if job.startswith(TRYON_PREFIX):
        return process_tryon(rt, job.removeprefix(TRYON_PREFIX))
    if job.startswith(ANALYSIS_PREFIX):
        return process_analysis(rt, int(job.removeprefix(ANALYSIS_PREFIX)))
    return process_look(rt, int(job))


def _fail_after_crash(job: str) -> None:
    if job.startswith(TRYON_PREFIX):
        model, rec_id = TryOn, job.removeprefix(TRYON_PREFIX)
    elif job.startswith(ANALYSIS_PREFIX):
        model, rec_id = PersonalAnalysis, int(job.removeprefix(ANALYSIS_PREFIX))
    else:
        model, rec_id = Look, int(job)
    with SessionLocal() as session:
        rec = session.get(model, rec_id)
        if rec is not None:
            _finish(session, rec, "failed", error="internal error")


def run_forever(rt: Runtime, stop_event: threading.Event | None = None) -> None:
    stop_event = stop_event or threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    recovered = rt.queue.recover()
    if recovered:
        log.warning("re-queued %d jobs left over from a previous run", recovered)
    log.info("worker started (llm=%s, catalog=%d products)", rt.llm.name, len(rt.catalog.index))
    next_trend_check = 0.0
    while not stop_event.is_set():
        if time.monotonic() >= next_trend_check:
            next_trend_check = time.monotonic() + TREND_CHECK_EVERY_S
            try:
                with SessionLocal() as session:
                    if refresh_if_due(session, rt.redis, rt.trend_researcher, rt.data_dir):
                        log.info("trends refreshed")
            except Exception:
                log.exception("trend refresh check failed")
        rt.queue.promote_due()
        job = rt.queue.reserve(timeout_s=1.0)
        if job is None:
            continue
        try:
            process_job(rt, job)
        except Exception:
            # A bug, not a flaky dependency: fail the job instead of retrying a poison message forever.
            log.exception("unexpected error on job %s", job)
            _fail_after_crash(job)
            rt.queue.ack(job)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The API process imports the catalog; the worker waits for it (compose orders startup).
    run_forever(build_runtime(get_settings(), import_catalog=False))


if __name__ == "__main__":
    main()
