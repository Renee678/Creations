"""The stylist step: Claude curates each outfit from the candidates the deterministic scorer picked.

Retrieval and scoring stay deterministic and tested (lookbook.py). The model only chooses, per slot, one of
the top few candidates so the outfit works as a whole, and says why in one line. Whatever it returns is
checked: an unknown id keeps the scorer's pick for that slot. On any error, no key or the daily cap, the
scorer's picks stand, so the lookbook never depends on the model.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone

from ..llm.client import LLMError
from ..llm.schemas import StylingRequest, StylingResult

log = logging.getLogger(__name__)

CACHE_TTL_S = 7 * 24 * 3600
WHY_MAX_WORDS = 12  # the line printed on a Look Book page: one short, casual sentence (Renee)
DANGLING = {"and", "or", "but", "with", "in", "on", "of", "for", "to", "a", "an", "the", "your", "its", "by", "as", "at"}


def short_why(text: str | None, max_words: int = WHY_MAX_WORDS) -> str | None:
    """One sentence of at most `max_words` words, cut at a word and never ending on "and" or "with"."""
    if not text or not text.strip():
        return None
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    words = first.split()
    if len(words) <= max_words:
        return first
    words = words[:max_words]
    while len(words) > 1 and (words[-1].lower().strip(",;:—-") in DANGLING or not words[-1].strip(",;:—-")):
        words.pop()
    return " ".join(words).rstrip(",;:—- ") + "."


Curated = dict[int, tuple[list[str], str, bool]]


def curate(llm, redis_client, request: StylingRequest, daily_limit: int) -> Curated:
    """Outfit index -> (one product id per slot, why it works or what clashes, approved).

    Empty when the stylist couldn't run: then the scorer's picks stand, unreviewed."""
    if not request.outfits:
        return {}
    body = request.model_dump_json()
    key = f"stylist:v3:{getattr(llm, 'name', '')}:{hashlib.sha256(body.encode()).hexdigest()}"
    cached = _cache_get(redis_client, key)
    if cached is None:
        if not _take_quota(redis_client, daily_limit):
            log.warning("stylist: daily limit reached, using the scorer's picks")
            return {}
        try:
            cached = llm.curate_outfits(request).model_dump_json()
        except (LLMError, ValueError) as e:
            log.warning("stylist failed, using the scorer's picks: %s", e)
            return {}
        _cache_set(redis_client, key, cached)
    return _validated(request, cached)


def _validated(request: StylingRequest, result_json: str) -> Curated:
    result = StylingResult.model_validate_json(result_json)
    by_index = {o.index: o for o in request.outfits}
    out = {}
    for styled in result.outfits:
        outfit = by_index.get(styled.index)
        if outfit is None:
            continue
        picks = []
        for i, slot in enumerate(outfit.slots):
            ids = [c.id for c in slot.candidates]
            pick = styled.picks[i] if i < len(styled.picks) else None
            picks.append(pick if pick in ids else (ids[0] if ids else ""))
        why = short_why(styled.why) if styled.approved else styled.why.strip()
        out[styled.index] = (picks, why or "", styled.approved)
    return out


def _cache_get(redis_client, key: str) -> str | None:
    try:
        value = redis_client.get(key)
    except Exception:  # a cache miss must never break the lookbook
        return None
    return value.decode() if isinstance(value, bytes) else value


def _cache_set(redis_client, key: str, value: str) -> None:
    try:
        redis_client.set(key, value, ex=CACHE_TTL_S)
    except Exception:
        pass


def _take_quota(redis_client, limit: int) -> bool:
    """Global daily cap on stylist calls, like the one on analyses: a cost circuit-breaker."""
    if limit <= 0:
        return True
    key = f"quota:stylist:{datetime.now(timezone.utc):%Y-%m-%d}"
    try:
        used = redis_client.incr(key)
        if used == 1:
            redis_client.expire(key, 2 * 24 * 3600)
    except Exception:
        return False
    return used <= limit
