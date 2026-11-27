from datetime import timedelta

from session_handoff.cache_ttl import detect_cache_ttl
from session_handoff.transcript import RequestUsage, Usage

ONE_HOUR = timedelta(hours=1)
FIVE_MINUTES = timedelta(minutes=5)


def request(**usage: int) -> RequestUsage:
    return RequestUsage("msg", None, Usage(**usage))


def test_one_hour_bucket_means_a_one_hour_ttl() -> None:
    assert detect_cache_ttl([request(ephemeral_1h_input_tokens=7)]) == ONE_HOUR


def test_five_minute_bucket_means_a_five_minute_ttl() -> None:
    assert detect_cache_ttl([request(ephemeral_5m_input_tokens=7)]) == FIVE_MINUTES


def test_five_minutes_wins_when_a_request_wrote_both_buckets() -> None:
    both = request(ephemeral_1h_input_tokens=7, ephemeral_5m_input_tokens=1)

    assert detect_cache_ttl([both]) == FIVE_MINUTES


def test_the_latest_request_that_wrote_decides_and_readers_are_skipped() -> None:
    requests = [
        request(ephemeral_5m_input_tokens=3),
        request(ephemeral_1h_input_tokens=3),
        request(cache_read_input_tokens=900),
    ]

    assert detect_cache_ttl(requests) == ONE_HOUR


def test_no_request_that_wrote_means_unknown() -> None:
    assert detect_cache_ttl([request(cache_read_input_tokens=9)]) is None
    assert detect_cache_ttl([]) is None
