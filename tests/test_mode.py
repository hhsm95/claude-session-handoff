import json
from datetime import UTC, datetime, timedelta

import pytest

from session_handoff.constants import (
    WARM_MAX_IDLE_FIVE_MINUTE_TTL_SECONDS,
    WARM_MAX_IDLE_ONE_HOUR_TTL_SECONDS,
)
from session_handoff.mode import Mode, ModeDecision, decide_mode, decide_mode_safely
from session_handoff.transcript import RequestUsage, Transcript, Usage, parse_transcript

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
ONE_HOUR = timedelta(seconds=WARM_MAX_IDLE_ONE_HOUR_TTL_SECONDS)
FIVE_MINUTES = timedelta(seconds=WARM_MAX_IDLE_FIVE_MINUTE_TTL_SECONDS)
SECOND = timedelta(seconds=1)


def writes_1h() -> Usage:
    return Usage(cache_creation_input_tokens=100, ephemeral_1h_input_tokens=100)


def writes_5m() -> Usage:
    return Usage(cache_creation_input_tokens=100, ephemeral_5m_input_tokens=100)


def reads_only() -> Usage:
    return Usage(cache_read_input_tokens=100)


def transcript_with(usages: list[Usage], last_request: datetime | None) -> Transcript:
    """Requests in order; every one but the last is a day older than the last."""
    stamps = [None if last_request is None else last_request - timedelta(days=1)] * len(usages)
    if usages:
        stamps[-1] = last_request
    requests = tuple(
        RequestUsage(f"msg_{i}", stamp, usage)
        for i, (usage, stamp) in enumerate(zip(usages, stamps, strict=True))
    )
    return Transcript(entries=(), requests=requests)


def idle_for(idle: timedelta, usages: list[Usage]) -> Transcript:
    return transcript_with(usages, NOW - idle)


def mode_of(transcript: Transcript, *, force_haiku: bool = False) -> Mode:
    return decide_mode(transcript, NOW, force_haiku=force_haiku).mode


def test_one_hour_cache_just_under_its_threshold_is_warm() -> None:
    assert mode_of(idle_for(ONE_HOUR - SECOND, [writes_1h()])) is Mode.WARM


@pytest.mark.parametrize(
    ("usage", "ttl"),
    [(writes_1h(), timedelta(hours=1)), (writes_5m(), timedelta(minutes=5))],
    ids=["1h", "5m"],
)
def test_an_idle_time_equal_to_the_cache_ttl_itself_is_always_cold(
    usage: Usage, ttl: timedelta
) -> None:
    assert mode_of(idle_for(ttl, [usage])) is Mode.COLD
    assert mode_of(idle_for(ttl + SECOND, [usage])) is Mode.COLD


def test_five_minute_cache_just_under_its_threshold_is_warm() -> None:
    assert mode_of(idle_for(FIVE_MINUTES - SECOND, [writes_5m()])) is Mode.WARM


def test_five_minute_cache_just_over_its_threshold_is_cold() -> None:
    assert mode_of(idle_for(FIVE_MINUTES + SECOND, [writes_5m()])) is Mode.COLD


def test_five_minute_cache_is_cold_at_an_idle_time_where_one_hour_would_be_warm() -> None:
    idle = FIVE_MINUTES + SECOND

    assert mode_of(idle_for(idle, [writes_5m()])) is Mode.COLD
    assert mode_of(idle_for(idle, [writes_1h()])) is Mode.WARM


def test_ttl_comes_from_the_last_request_that_wrote_to_the_cache() -> None:
    idle = FIVE_MINUTES + SECOND

    assert mode_of(idle_for(idle, [writes_5m(), writes_1h()])) is Mode.WARM
    assert mode_of(idle_for(idle, [writes_1h(), writes_5m()])) is Mode.COLD


def test_requests_that_only_read_are_skipped_when_finding_the_ttl() -> None:
    idle = FIVE_MINUTES + SECOND

    assert mode_of(idle_for(idle, [writes_1h(), reads_only(), reads_only()])) is Mode.WARM


def test_five_minutes_wins_when_a_request_wrote_both_ttls() -> None:
    # The 5m part is the conversation tail, so that is what expires first.
    both = Usage(ephemeral_1h_input_tokens=10, ephemeral_5m_input_tokens=10)

    assert mode_of(idle_for(FIVE_MINUTES + SECOND, [both])) is Mode.COLD
    assert mode_of(idle_for(FIVE_MINUTES - SECOND, [both])) is Mode.WARM


def test_unknown_ttl_is_cold_even_when_the_idle_time_is_tiny() -> None:
    assert mode_of(idle_for(SECOND, [reads_only(), Usage()])) is Mode.COLD


def test_no_requests_at_all_is_cold() -> None:
    assert mode_of(idle_for(SECOND, [])) is Mode.COLD


def test_no_timestamps_is_cold_and_has_no_idle_time() -> None:
    decision = decide_mode(transcript_with([writes_1h()], None), NOW, force_haiku=False)

    assert decision == ModeDecision(Mode.COLD, idle_minutes=None)


def test_naive_timestamp_is_cold() -> None:
    naive = NOW.replace(tzinfo=None) - SECOND

    assert mode_of(transcript_with([writes_1h()], naive)) is Mode.COLD


def test_timestamp_in_the_future_is_cold() -> None:
    assert mode_of(transcript_with([writes_1h()], NOW + SECOND)) is Mode.COLD


def test_idle_time_is_measured_from_the_latest_request() -> None:
    older = Usage(cache_creation_input_tokens=1, ephemeral_5m_input_tokens=1)
    transcript = transcript_with([older, older], NOW - SECOND)

    assert mode_of(transcript) is Mode.WARM


def request_line(when: datetime) -> str:
    message = {
        "id": "msg_old",
        "role": "assistant",
        "model": "claude-opus-4-1",
        "content": [{"type": "text", "text": "answer"}],
        "usage": {"cache_creation": {"ephemeral_1h_input_tokens": 100}},
    }
    return json.dumps({"type": "assistant", "timestamp": when.isoformat(), "message": message})


def local_user_line(content: str, when: datetime, **extra: object) -> str:
    record = {
        "type": "user",
        "timestamp": when.isoformat(),
        "message": {"role": "user", "content": content},
        **extra,
    }
    return json.dumps(record)


def test_entries_that_sent_no_request_do_not_refresh_the_idle_time() -> None:
    old = NOW - timedelta(hours=2)
    recent = NOW - SECOND
    lines = [
        request_line(old),
        local_user_line("<bash-input>ls</bash-input>", recent),
        local_user_line("<bash-stdout>a.py</bash-stdout>", recent),
        local_user_line("<command-name>/context</command-name>", recent),
        local_user_line("<local-command-stdout>ctx</local-command-stdout>", recent),
        local_user_line("<local-command-caveat>x</local-command-caveat>", recent, isMeta=True),
        local_user_line("a prompt that was blocked", recent),
    ]

    assert mode_of(parse_transcript(lines)) is Mode.COLD


def test_force_haiku_is_cold_even_with_a_live_cache() -> None:
    transcript = idle_for(SECOND, [writes_1h()])

    assert mode_of(transcript, force_haiku=False) is Mode.WARM
    assert mode_of(transcript, force_haiku=True) is Mode.COLD


def test_decision_reports_whole_idle_minutes() -> None:
    idle = timedelta(minutes=30, seconds=59)

    decision = decide_mode(idle_for(idle, [writes_1h()]), NOW, force_haiku=False)

    assert decision == ModeDecision(Mode.WARM, idle_minutes=30)


def test_forced_decision_still_reports_the_idle_minutes() -> None:
    decision = decide_mode(idle_for(timedelta(minutes=3), [writes_1h()]), NOW, force_haiku=True)

    assert decision == ModeDecision(Mode.COLD, idle_minutes=3)


def test_safe_decision_matches_the_plain_one_when_nothing_fails() -> None:
    transcript = idle_for(SECOND, [writes_1h()])

    assert decide_mode_safely(lambda: transcript, NOW, force_haiku=False) == decide_mode(
        transcript, NOW, force_haiku=False
    )


def test_exception_while_reading_the_transcript_is_cold() -> None:
    def unreadable() -> Transcript:
        raise OSError("transcript vanished")

    decision = decide_mode_safely(unreadable, NOW, force_haiku=False)

    assert decision == ModeDecision(Mode.COLD, idle_minutes=None)


def test_exception_while_deciding_is_cold() -> None:
    broken = Transcript(entries=(), requests=None)  # type: ignore[arg-type]

    assert decide_mode_safely(lambda: broken, NOW, force_haiku=False).mode is Mode.COLD


@pytest.mark.parametrize("force_haiku", [False, True])
def test_safe_decision_never_raises(force_haiku: bool) -> None:
    def explode() -> Transcript:
        raise RuntimeError("boom")

    assert decide_mode_safely(explode, NOW, force_haiku=force_haiku).mode is Mode.COLD
