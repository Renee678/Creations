"""Background worker: turns queued look uploads into look-alike results.

Run with `python -m stylebuddy.worker`. Each job is handled idempotently, since
the queue delivers at least once.
"""

import logging
import signal
import time

from .config import get_settings
from .db import SessionLocal
from .llm.client import LLMError
from .models import Look, User, utcnow
from .runtime import Runtime, build_runtime
from .services.dupes import find_dupes
from .services.style_memory import record_look, user_context
from .services.trends import refresh_if_due

log = logging.getLogger("stylebuddy.worker")

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0
TREND_CHECK_EVERY_S = 600


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
        try:
            analysis = rt.llm.analyze_look(look.image, look.media_type)
        except LLMError as e:
            if e.retryable and look.attempts < MAX_ATTEMPTS:
                delay = BACKOFF_BASE_S * 2 ** (look.attempts - 1)
                log.warning("look %s attempt %s failed (%s); retrying in %.0fs", look_id, look.attempts, e, delay)
                look.status, look.error = "queued", str(e)
                session.commit()
                rt.queue.retry_later(look_id, delay)
                return "queued"
            log.error("look %s failed permanently: %s", look_id, e)
            _finish(session, look, "failed", error=str(e))
            rt.queue.ack(look_id)
            return "failed"

        if not analysis.is_outfit or not analysis.items:
            _finish(session, look, "failed", error="图片里没有识别到衣服，换一张穿搭图试试？")
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


def _finish(session, look: Look, status: str, result: dict | None = None, error: str | None = None) -> None:
    look.status, look.result, look.error = status, result, error
    look.model = (result or {}).get("model")
    look.image = None  # drop the photo as soon as we're done with it
    look.finished_at = utcnow()
    session.commit()


def run_forever(rt: Runtime) -> None:
    stopping = False

    def _stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    recovered = rt.queue.recover()
    if recovered:
        log.warning("re-queued %d jobs left over from a previous run", recovered)
    log.info("worker started (llm=%s, catalog=%d products)", rt.llm.name, len(rt.catalog.index))
    next_trend_check = 0.0
    while not stopping:
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
            process_look(rt, int(job))
        except Exception:
            # A bug, not a flaky dependency: fail the job instead of retrying a poison message forever.
            log.exception("unexpected error on look %s", job)
            with SessionLocal() as session:
                look = session.get(Look, int(job))
                if look is not None:
                    _finish(session, look, "failed", error="internal error")
            rt.queue.ack(job)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The API process imports the catalog; the worker waits for it (compose orders startup).
    run_forever(build_runtime(get_settings(), import_catalog=False))


if __name__ == "__main__":
    main()
