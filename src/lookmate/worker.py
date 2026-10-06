"""Background worker: turns queued uploads into look-alike results, personal analyses and try-ons.

Run with `python -m lookmate.worker`. Each job is handled idempotently, since
the queue delivers at least once.
"""

import base64
import logging
import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace

from .config import get_settings
from .db import SessionLocal
from .llm.client import LLMError
from .models import BodyModel, Look, PersonalAnalysis, TryOn, User, utcnow
from .runtime import Runtime, build_runtime, reload_catalog_if_changed
from .services.dupes import find_dupes
from .services.style_memory import record_look, user_context
from .services.trends import refresh_if_due
from .services.vocab import STYLES
from .tryon.client import TRYON_STAGE, TryOnError, plan_steps, tryon_quota_key
from .tryon.face import face_crop, paste_head
from .tryon.garments import cached_photo, garment_for, garment_from_words

log = logging.getLogger("lookmate.worker")

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0
CATALOG_CHECK_EVERY_S = 15.0
GARMENT_BUDGET_S = 12.0  # all garment photos, fetched together; a cache hit is instant
TREND_CHECK_EVERY_S = 600


ANALYSIS_PREFIX = "analysis:"
TRYON_PREFIX = "tryon:"
MODEL_PREFIX = "model:"
NO_MODEL_NOTE = "Saved your photo as it is: drawing a studio model needs a REPLICATE_API_TOKEN."


def analysis_job(analysis_id: int) -> str:
    """Queue key for a personal analysis; looks use their bare id."""
    return f"{ANALYSIS_PREFIX}{analysis_id}"


def tryon_job(tryon_id: str) -> str:
    return f"{TRYON_PREFIX}{tryon_id}"


def model_job(model_id: str) -> str:
    return f"{MODEL_PREFIX}{model_id}"


def process_model(rt: Runtime, model_id: str) -> str:
    """Draw "My model" from the uploaded full-body photo: one Nano Banana call."""
    job = model_job(model_id)
    with SessionLocal() as session:
        rec = session.get(BodyModel, model_id)
        if rec is None:
            rt.queue.ack(job)
            return "missing"
        if rec.status in ("done", "failed"):  # duplicate delivery: already handled
            rt.queue.ack(job)
            return rec.status
        if rt.model_maker is None:  # the token went away after this was queued
            rec.result_image, rec.result_media_type = rec.photo, rec.media_type
            _finish(session, rec, "done", result={"generated": False, "note": NO_MODEL_NOTE, "timings_ms": {}})
            rt.queue.ack(job)
            return "done"

        rec.status, rec.attempts = "processing", rec.attempts + 1
        session.commit()
        user = session.get(User, rec.user_id)
        started = time.monotonic()
        face = {"face": (rec.face_photo, rec.face_media_type or "image/jpeg")} if rec.face_photo else {}
        out = _call_llm(rt, session, rec, job, lambda: rt.model_maker.make_model(
            rec.photo, rec.media_type, user.height_cm if user else None, user.weight_kg if user else None, **face))
        if out is None:
            return rec.status
        # The model-maker says which model drew it (Pro, or the fallback); a simpler one returns two values.
        image, media_type, used = (*out, rt.model_maker.name)[:3]
        rec.result_image, rec.result_media_type = image, media_type
        model_ms = round((time.monotonic() - started) * 1000)
        _finish(session, rec, "done", result={
            "generated": True, "note": None, "model": used, "face_photo": bool(face),
            "timings_ms": {"model_ms": model_ms},
        })
        rt.queue.ack(job)
        log.info("my model %s done in %s ms", model_id, model_ms)
        return "done"


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


HEADWEAR = re.compile(r"\b(hats?|caps?|beanies?|berets?|fedoras?|bucket hat|headbands?|hair clips?|hoods?|balaclavas?|"
                      r"visors?|headwear|headscarf|bandanas?|turbans?)\b", re.I)


def missing_photo_message(products) -> str:
    names = [p.name for p in products]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return (f"We couldn't load the photo for {listed}, so we won't guess what it looks like. "
            "Try again, or swap it for another piece.")


def _on_the_head(p) -> bool:
    return p.category == "accessory" and bool(HEADWEAR.search(f"{p.name} {p.product_type}"))


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

        meta: dict[str, bool | str] = {}

        timings: dict[str, int] = {}

        def stage(name: str) -> None:
            rt.redis.set(TRYON_STAGE.format(tryon_id), name, ex=900)

        def fetch(products) -> dict:
            """Every piece's photo at once, within one budget. A piece without one stops the try-on before the
            model is called: a garment drawn from its description comes out wrong (Renee: precision over fill)."""
            t0 = time.monotonic()
            stage("fetching")
            out, missing = {}, []
            pool = ThreadPoolExecutor(max_workers=max(1, len(products)))
            futures = {pool.submit(garment_for, p, rt.data_dir): p for p in products}
            done_f, _ = wait(futures, timeout=GARMENT_BUDGET_S)
            pool.shutdown(wait=False)  # a straggler still lands in the disk cache for next time
            for f, p in futures.items():
                garment = None
                if f in done_f:
                    try:
                        garment = f.result()
                    except TryOnError as e:
                        if e.retryable:
                            raise
                        log.warning("try-on %s: no photo for %s (%s)", tryon_id, p.id, e)
                if garment is None or garment.image is None:
                    # The fitting room's prefetch (server or browser) may have landed in the cache meanwhile.
                    hit = cached_photo(p.image_url, rt.data_dir) if p.image_url.startswith("https://") else None
                    if hit is not None:
                        garment = replace(garment or garment_from_words(p), image=hit[0], media_type=hit[1])
                if garment is not None and (garment.image is not None
                                            or (garment.url and getattr(rt.tryon, "fetches_urls", False))):
                    out[p.id] = garment  # a photo, or a URL the provider loads itself (FASHN, IDM-VTON)
                else:
                    missing.append(p)
            timings["fetch_ms"] = round((time.monotonic() - t0) * 1000)
            if missing:
                log.warning("try-on %s: no photo for %s; not rendering", tryon_id, ", ".join(p.id for p in missing))
                if get_settings().daily_tryon_limit > 0:
                    rt.redis.decr(tryon_quota_key())  # the model was never called, so this one isn't counted
                raise TryOnError(missing_photo_message(missing))
            return out

        def model(call, *args):
            t0 = time.monotonic()
            stage("dressing")
            result = call(*args)
            timings["model_ms"] = timings.get("model_ms", 0) + round((time.monotonic() - t0) * 1000)
            return result

        def render():
            image, media_type, done = rec.photo, rec.media_type, []
            if rt.tryon.renders and getattr(rt.tryon, "whole_outfit", False):
                # One call for the whole outfit, shoes and bags included.
                fetched = fetch(pieces)
                garments = [(p.id, fetched[p.id]) for p in pieces]
                face = None
                if rec.from_model and getattr(rt.tryon, "takes_face", False):
                    # My model is a full-body shot, so its face is tiny: send a close-up too (tryon.face).
                    face = face_crop(rec.photo)
                    meta["face_reference"] = face is not None
                args = (image, media_type, [g for _, g in garments]) + ((face,) if face else ())
                image, media_type = model(rt.tryon.dress_outfit, *args)
                if rec.from_model:
                    # Safety net: put My model's own head back when the frames line up and nothing sits on it.
                    t0 = time.monotonic()
                    worn = [p.name for p in pieces if _on_the_head(p)]
                    pasted, why = (None, "headwear") if worn else paste_head(rec.photo, image)
                    if pasted:
                        image, media_type = pasted
                    else:
                        meta["head_paste_skipped"] = why
                        log.info("try-on %s: head not pasted back (%s%s)", tryon_id, why,
                                 f": {', '.join(worn)}" if worn else "")
                    meta["head_pasted"] = pasted is not None
                    timings["paste_ms"] = round((time.monotonic() - t0) * 1000)
                return image, media_type, [pid for pid, _ in garments]
            if rt.tryon.renders:
                fetched = fetch([step["product"] for step in steps])
                for step in steps:
                    image, media_type = model(rt.tryon.dress, image, media_type, fetched[step["product"].id])
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
            "timings_ms": timings,
            **meta,
        })
        rt.redis.delete(TRYON_STAGE.format(tryon_id))
        rt.queue.ack(job)
        log.info("try-on %s done (%s, %d pieces) in %s ms: %s", tryon_id, rt.tryon.name, len(done),
                 rec.result["latency_ms"], timings)
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
    elif isinstance(rec, BodyModel):
        rec.photo = rec.face_photo = None
    else:
        rec.photos = None
    rec.finished_at = utcnow()
    session.commit()


def process_job(rt: Runtime, job: str) -> str:
    if job.startswith(MODEL_PREFIX):
        return process_model(rt, job.removeprefix(MODEL_PREFIX))
    if job.startswith(TRYON_PREFIX):
        return process_tryon(rt, job.removeprefix(TRYON_PREFIX))
    if job.startswith(ANALYSIS_PREFIX):
        return process_analysis(rt, int(job.removeprefix(ANALYSIS_PREFIX)))
    return process_look(rt, int(job))


def _fail_after_crash(job: str) -> None:
    if job.startswith(MODEL_PREFIX):
        model, rec_id = BodyModel, job.removeprefix(MODEL_PREFIX)
    elif job.startswith(TRYON_PREFIX):
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
    """Serve looks and analyses here, and try-ons on a thread of their own.

    A render can take minutes when the model is cold; on one shared loop that held up every
    look and analysis queued behind it.
    """
    stop_event = stop_event or threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    log.info("worker started (llm=%s, catalog=%d products)", rt.llm.name, len(rt.catalog.index))
    shares = []
    if rt.tryon_queue is not None:
        tryons = replace(rt, queue=rt.tryon_queue)  # handlers ack and retry on rt.queue
        shares.append(tryons)
        threading.Thread(target=_serve, args=(tryons, stop_event, False), daemon=True, name="tryon-worker").start()
    _serve(rt, stop_event, True, shares)


def _check_trends(rt: Runtime) -> None:
    try:
        with SessionLocal() as session:
            if refresh_if_due(session, rt.redis, rt.trend_researcher, rt.data_dir):
                log.info("trends refreshed")
    except Exception:
        log.exception("trend refresh check failed")


def _serve(rt: Runtime, stop_event: threading.Event, check_trends: bool, shares: list[Runtime] = ()) -> None:
    recovered = rt.queue.recover()
    if recovered:
        log.warning("re-queued %d jobs left over from a previous run", recovered)
    next_trend_check = 0.0 if check_trends else float("inf")
    trend_thread: threading.Thread | None = None
    next_catalog_check = 0.0 if check_trends else float("inf")
    while not stop_event.is_set():
        if time.monotonic() >= next_catalog_check:
            # The API imports a new catalog in the background; pick it up here (and on the try-on thread).
            next_catalog_check = time.monotonic() + CATALOG_CHECK_EVERY_S
            try:
                if reload_catalog_if_changed(rt):
                    for other in shares:
                        other.catalog, other.catalog_version = rt.catalog, rt.catalog_version
            except Exception:
                log.exception("catalog reload check failed")
        if time.monotonic() >= next_trend_check:
            next_trend_check = time.monotonic() + TREND_CHECK_EVERY_S
            # Research with web search takes minutes: run it beside the queue, not in front of it.
            if trend_thread is None or not trend_thread.is_alive():
                trend_thread = threading.Thread(target=_check_trends, args=(rt,), daemon=True, name="trends")
                trend_thread.start()
        rt.queue.promote_due()
        job = rt.queue.reserve(timeout_s=1.0)
        if job is None:
            continue
        if check_trends and job.startswith((TRYON_PREFIX, MODEL_PREFIX)) and rt.tryon_queue is not None:
            # queued before try-ons had their own queue: hand it over instead of blocking here
            rt.tryon_queue.enqueue(job)
            rt.queue.ack(job)
            continue
        try:
            process_job(rt, job)
        except Exception:
            # A bug, not a flaky dependency: fail the job instead of retrying a poison message forever.
            log.exception("unexpected error on job %s", job)
            _fail_after_crash(job)
            rt.queue.ack(job)
    if trend_thread is not None:
        trend_thread.join(timeout=10)  # let a quick check finish; a long research is a daemon and is dropped


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The API process imports the catalog; the worker waits for it (compose orders startup).
    run_forever(build_runtime(get_settings(), import_catalog=False))


if __name__ == "__main__":
    main()
