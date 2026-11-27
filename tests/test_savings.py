from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from session_handoff.savings import BoundaryMeasurement, SavingsStatus, measure_boundaries
from session_handoff.transcript import (
    CompactBoundary,
    RequestUsage,
    TimelineItem,
    Usage,
    read_timeline,
)

COMPACTION_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "session_compaction_2_1_332.jsonl"
)

START = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
ONE_HOUR_BUCKET = {"ephemeral_1h_input_tokens": 1}
FIVE_MINUTE_BUCKET = {"ephemeral_5m_input_tokens": 1}


def request(
    minutes: float,
    *,
    input_tokens: int = 0,
    cache_creation: int = 0,
    buckets: dict[str, int] | None = None,
) -> RequestUsage:
    return RequestUsage(
        message_id=f"msg_{minutes}",
        timestamp=START + timedelta(minutes=minutes),
        usage=Usage(
            input_tokens=input_tokens,
            cache_creation_input_tokens=cache_creation,
            **(buckets if buckets is not None else ONE_HOUR_BUCKET),
        ),
    )


def boundary(
    uuid: str = "b1",
    *,
    minutes: float = 0,
    pre: int | None = 1000,
    post: int | None = 100,
    trigger: str | None = "auto",
) -> CompactBoundary:
    return CompactBoundary(
        uuid=uuid,
        timestamp=START + timedelta(minutes=minutes),
        trigger=trigger,
        pre_tokens=pre,
        post_tokens=post,
        session_id="s1",
        cwd="/work",
    )


def measure_one(*items: TimelineItem) -> BoundaryMeasurement:
    (measurement,) = measure_boundaries(items)
    return measurement


def test_expired_cache_counterfactual_is_the_whole_pre_compaction_context() -> None:
    measurement = measure_one(
        request(0),
        boundary(pre=5000, post=300, minutes=55),
        request(70, input_tokens=10, cache_creation=400),
    )

    assert measurement.status is SavingsStatus.MEASURED
    assert measurement.gap_seconds == 70 * 60
    assert measurement.ttl_seconds == 60 * 60
    assert measurement.counterfactual_uncached == 5000
    assert measurement.actual_uncached == 410
    assert measurement.saved == 4590
    assert measurement.compaction_cached_read == 5000
    assert measurement.compaction_output == 300


def test_alive_cache_counterfactual_is_the_usual_incremental_write_and_saved_is_negative() -> None:
    measurement = measure_one(
        request(0, input_tokens=3, cache_creation=221),
        boundary(pre=9000, post=100),
        request(58, input_tokens=7, cache_creation=800),
    )

    assert measurement.counterfactual_uncached == 224
    assert measurement.actual_uncached == 807
    assert measurement.saved == 224 - 807


def test_a_gap_exactly_at_the_ttl_still_counts_as_alive() -> None:
    measurement = measure_one(
        request(0, input_tokens=1, cache_creation=2),
        boundary(pre=9000),
        request(60, input_tokens=4),
    )

    assert measurement.counterfactual_uncached == 3


def test_five_minute_ttl_expires_after_five_minutes() -> None:
    items = (
        request(0, buckets=FIVE_MINUTE_BUCKET),
        boundary(pre=7000),
        request(6, cache_creation=50),
    )

    measurement = measure_one(*items)

    assert measurement.ttl_seconds == 5 * 60
    assert measurement.counterfactual_uncached == 7000


def test_ttl_is_taken_from_the_last_request_that_wrote_before_the_boundary() -> None:
    measurement = measure_one(
        request(0, buckets=FIVE_MINUTE_BUCKET),
        request(1, buckets=ONE_HOUR_BUCKET),
        request(2, buckets={"cache_read_input_tokens": 99}),
        boundary(pre=7000),
        request(30),
    )

    assert measurement.ttl_seconds == 60 * 60


def test_boundary_without_a_later_request_is_unused_with_overhead_and_zero_saved() -> None:
    measurement = measure_one(request(0), boundary(pre=800, post=40))

    assert measurement.status is SavingsStatus.UNUSED
    assert measurement.saved == 0
    assert measurement.compaction_cached_read == 800
    assert measurement.compaction_output == 40
    assert measurement.gap_seconds is None


def test_unknown_ttl_is_unknown_and_has_no_savings() -> None:
    measurement = measure_one(
        request(0, buckets={"cache_read_input_tokens": 5}),
        boundary(pre=800, post=40),
        request(90, cache_creation=10),
    )

    assert measurement.status is SavingsStatus.UNKNOWN
    assert measurement.ttl_seconds is None
    assert measurement.saved == 0
    assert measurement.compaction_cached_read == 800


def test_boundary_with_no_request_before_it_has_an_unknown_ttl() -> None:
    measurement = measure_one(boundary(), request(90))

    assert measurement.status is SavingsStatus.UNKNOWN


def test_missing_metadata_is_unknown_even_without_a_later_request() -> None:
    measurement = measure_one(request(0), boundary(pre=None, post=None))

    assert measurement.status is SavingsStatus.UNKNOWN
    assert measurement.pre_tokens is None
    assert measurement.compaction_cached_read == 0
    assert measurement.compaction_output == 0


def test_only_one_missing_token_count_is_enough_to_be_unknown() -> None:
    measurement = measure_one(request(0), boundary(pre=1000, post=None), request(90))

    assert measurement.status is SavingsStatus.UNKNOWN


def test_requests_without_timestamps_cannot_give_a_gap_and_are_unknown() -> None:
    undated = RequestUsage("m", None, Usage(ephemeral_1h_input_tokens=1))

    measurement = measure_one(request(0), boundary(), undated)

    assert measurement.status is SavingsStatus.UNKNOWN


def test_several_boundaries_in_one_file_each_use_their_own_neighbours() -> None:
    measurements = measure_boundaries(
        [
            request(0, input_tokens=1),
            boundary("first", pre=5000, minutes=55),
            request(70, cache_creation=100),
            boundary("second", pre=9000, minutes=75),
            request(200, cache_creation=300),
            boundary("third", pre=100),
        ]
    )

    assert [(m.boundary_uuid, m.status) for m in measurements] == [
        ("first", SavingsStatus.MEASURED),
        ("second", SavingsStatus.MEASURED),
        ("third", SavingsStatus.UNUSED),
    ]
    assert measurements[0].gap_seconds == 70 * 60
    assert measurements[1].gap_seconds == (200 - 70) * 60
    assert measurements[1].counterfactual_uncached == 9000
    assert measurements[1].actual_uncached == 300


def test_a_request_belongs_only_to_the_last_boundary_before_it() -> None:
    measurements = measure_boundaries(
        [
            request(0),
            boundary("first", minutes=10, pre=700, post=70),
            boundary("second", minutes=20),
            request(90),
        ]
    )

    first, second = measurements
    assert (first.status, second.status) == (SavingsStatus.UNUSED, SavingsStatus.MEASURED)
    assert first.saved == 0
    assert (first.compaction_cached_read, first.compaction_output) == (700, 70)


def test_a_negative_gap_from_a_clock_skew_is_unknown() -> None:
    measurement = measure_one(request(10), boundary(minutes=12), request(5, cache_creation=50))

    assert measurement.status is SavingsStatus.UNKNOWN
    assert measurement.saved == 0


def test_idle_before_is_the_time_from_the_last_earlier_request_to_the_boundary() -> None:
    measurement = measure_one(request(0), request(4), boundary(minutes=10), request(20))

    assert measurement.idle_before_seconds == 6 * 60


def test_idle_before_is_kept_for_a_boundary_that_is_unused_or_unknown() -> None:
    unused = measure_one(request(0), boundary(minutes=45))
    unknown = measure_one(request(0), boundary(minutes=30, pre=None, post=None))

    assert unused.idle_before_seconds == 45 * 60
    assert unknown.idle_before_seconds == 30 * 60


def test_idle_before_is_none_without_a_dated_request_before_the_boundary() -> None:
    undated = RequestUsage("m", None, Usage(ephemeral_1h_input_tokens=1))

    assert measure_one(boundary(minutes=10), request(20)).idle_before_seconds is None
    assert measure_one(undated, boundary(minutes=10)).idle_before_seconds is None


def test_idle_before_is_none_when_the_clock_runs_backwards() -> None:
    assert measure_one(request(30), boundary(minutes=10)).idle_before_seconds is None


def test_measurement_carries_the_boundary_identity() -> None:
    measurement = measure_one(boundary("abc", minutes=3, trigger="manual"))

    assert measurement.boundary_uuid == "abc"
    assert measurement.trigger == "manual"
    assert measurement.timestamp == START + timedelta(minutes=3)
    assert (measurement.session_id, measurement.cwd) == ("s1", "/work")


def test_no_boundaries_means_no_measurements() -> None:
    assert measure_boundaries([request(0), request(1)]) == []


@pytest.mark.parametrize("trigger", ["auto", "manual", None])
def test_trigger_does_not_change_the_rule(trigger: str | None) -> None:
    measurement = measure_one(
        request(0), boundary(pre=500, trigger=trigger), request(90, cache_creation=20)
    )

    assert measurement.saved == 480


def test_fixture_timeline_ignores_sidechain_and_keeps_boundary_without_metadata_unknown() -> None:
    measurements = measure_boundaries(read_timeline(COMPACTION_FIXTURE))

    by_uuid = {m.boundary_uuid: m for m in measurements}
    assert set(by_uuid) == {"cb-auto", "cb-manual", "cb-bare"}
    auto = by_uuid["cb-auto"]
    assert auto.status is SavingsStatus.MEASURED
    assert auto.saved == 238796 - (135 + 59734)
    assert by_uuid["cb-manual"].status is SavingsStatus.UNKNOWN
    assert by_uuid["cb-bare"].status is SavingsStatus.UNKNOWN
