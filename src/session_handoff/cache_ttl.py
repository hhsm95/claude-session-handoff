"""Prompt cache TTL of a conversation, read from which cache bucket its requests wrote to."""

from collections.abc import Sequence
from datetime import timedelta

from session_handoff.constants import CACHE_TTL_FIVE_MINUTE_SECONDS, CACHE_TTL_ONE_HOUR_SECONDS
from session_handoff.transcript import RequestUsage


def detect_cache_ttl(requests: Sequence[RequestUsage]) -> timedelta | None:
    """TTL of the latest request that wrote to the cache, `None` when none did."""
    for request in reversed(requests):
        # The 5m tokens are the conversation tail, so they expire first even beside 1h tokens.
        if request.usage.ephemeral_5m_input_tokens > 0:
            return timedelta(seconds=CACHE_TTL_FIVE_MINUTE_SECONDS)
        if request.usage.ephemeral_1h_input_tokens > 0:
            return timedelta(seconds=CACHE_TTL_ONE_HOUR_SECONDS)
    return None
