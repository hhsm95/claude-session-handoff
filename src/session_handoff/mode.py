"""Warm or cold handoff, decided conservatively from the prompt cache TTL and the idle time."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from session_handoff.cache_ttl import detect_cache_ttl
from session_handoff.constants import (
    CACHE_TTL_FIVE_MINUTE_SECONDS,
    SECONDS_PER_MINUTE,
    WARM_MAX_IDLE_FIVE_MINUTE_TTL_SECONDS,
    WARM_MAX_IDLE_ONE_HOUR_TTL_SECONDS,
)
from session_handoff.transcript import Transcript


class Mode(Enum):
    WARM = "warm"
    COLD = "cold"


@dataclass(frozen=True)
class ModeDecision:
    mode: Mode
    idle_minutes: int | None
    """Whole minutes since the last main-thread request, `None` when it cannot be known."""


_UNKNOWN = ModeDecision(Mode.COLD, idle_minutes=None)


def decide_mode(transcript: Transcript, now: datetime, *, force_haiku: bool) -> ModeDecision:
    """Warm only if the cache TTL is known and the idle time is under that TTL's threshold."""
    idle = _idle_time(transcript, now)
    if idle is None:
        return _UNKNOWN
    idle_minutes = int(idle.total_seconds() // SECONDS_PER_MINUTE)
    max_idle = _max_warm_idle(transcript)
    is_warm = not force_haiku and max_idle is not None and idle < max_idle
    return ModeDecision(Mode.WARM if is_warm else Mode.COLD, idle_minutes)


def decide_mode_safely(
    read_transcript: Callable[[], Transcript], now: datetime, *, force_haiku: bool
) -> ModeDecision:
    """Like `decide_mode`, but reading or deciding that fails in any way is just cold."""
    try:
        return decide_mode(read_transcript(), now, force_haiku=force_haiku)
    except Exception:  # no failure may escape as warm
        return _UNKNOWN


def _max_warm_idle(transcript: Transcript) -> timedelta | None:
    ttl = detect_cache_ttl(transcript.requests)
    if ttl is None:
        return None
    if ttl == timedelta(seconds=CACHE_TTL_FIVE_MINUTE_SECONDS):
        return timedelta(seconds=WARM_MAX_IDLE_FIVE_MINUTE_TTL_SECONDS)
    return timedelta(seconds=WARM_MAX_IDLE_ONE_HOUR_TTL_SECONDS)


def _idle_time(transcript: Transcript, now: datetime) -> timedelta | None:
    """Time since the last main-thread request; local entries sent none and do not count."""
    if now.tzinfo is None or not transcript.requests:
        return None
    last_request = transcript.requests[-1].timestamp
    if last_request is None or last_request.tzinfo is None:
        return None
    idle = now - last_request
    return idle if idle >= timedelta(0) else None
