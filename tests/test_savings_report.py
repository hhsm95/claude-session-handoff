from dataclasses import replace

from session_handoff.constants import IDLE_COMPACTION_MIN_IDLE_SECONDS
from session_handoff.ledger import LedgerRecord
from session_handoff.savings import SavingsStatus
from session_handoff.savings_report import render_report

BASE = LedgerRecord(
    session_id="s1",
    boundary_uuid="b",
    project="p",
    timestamp=None,
    trigger="auto",
    status=SavingsStatus.MEASURED,
    pre_tokens=None,
    post_tokens=None,
    gap_seconds=None,
    ttl_seconds=None,
    counterfactual_uncached=0,
    actual_uncached=0,
    saved=0,
    compaction_cached_read=0,
    compaction_output=0,
    plugin_version="x",
    recorded_at="t",
)


def measured(
    *,
    trigger: str = "auto",
    without: int,
    with_: int,
    cached_read: int = 0,
    output: int = 0,
    idle_before: int | None = None,
) -> LedgerRecord:
    return replace(
        BASE,
        trigger=trigger,
        idle_before_seconds=idle_before,
        counterfactual_uncached=without,
        actual_uncached=with_,
        saved=without - with_,
        compaction_cached_read=cached_read,
        compaction_output=output,
    )


def unused(*, trigger: str = "auto", cached_read: int = 0, output: int = 0) -> LedgerRecord:
    return replace(
        BASE,
        trigger=trigger,
        status=SavingsStatus.UNUSED,
        compaction_cached_read=cached_read,
        compaction_output=output,
    )


def test_report_shows_counts_totals_percentage_overhead_and_triggers() -> None:
    records = [
        measured(without=1_000_000, with_=250_000, cached_read=1_000_000, output=10_000),
        measured(without=500_000, with_=250_000, cached_read=600_000, output=5_000),
        measured(trigger="manual", without=500_000, with_=500_000, cached_read=0, output=0),
        unused(cached_read=400_000, output=2_000),
        replace(BASE, status=SavingsStatus.UNKNOWN),
    ]

    report = render_report(records)

    assert report.splitlines() == [
        "Compaction savings (4 compactions: 3 measured, 1 unused)",
        "  Uncached tokens without compacting: 2,000,000",
        "  Uncached tokens with compacting:    1,000,000",
        "  Saved: 1,000,000 tokens (50.0%)",
        "  Compaction overhead: 2,000,000 cached read, 17,000 output",
        "  By trigger: auto 3 (66.7%) · manual 1 (0.0%)",
        "  Not measurable (unknown cache TTL or metadata): 1",
        'Estimate: "without compacting" assumes the cache expired when the gap exceeded its TTL.',
    ]


def test_automatic_compactions_split_into_idle_and_limit_by_the_idle_before_them() -> None:
    records = [
        measured(without=1_000, with_=200, idle_before=IDLE_COMPACTION_MIN_IDLE_SECONDS),
        measured(without=1_000, with_=900, idle_before=IDLE_COMPACTION_MIN_IDLE_SECONDS - 1),
        measured(without=400, with_=400, idle_before=None),
        measured(trigger="manual", without=500, with_=250, idle_before=10 * 3_600),
    ]

    report = render_report(records)

    assert (
        "  By trigger: auto 1 (0.0%) · auto idle 1 (80.0%) · auto limit 1 (10.0%)"
        " · manual 1 (50.0%)"
    ) in report.splitlines()


def test_idle_and_limit_each_sum_their_own_compactions() -> None:
    records = [
        measured(without=1_000, with_=0, idle_before=2 * 3_600),
        measured(without=1_000, with_=500, idle_before=3 * 3_600),
        measured(without=1_000, with_=1_000, idle_before=60),
        unused(),
    ]

    report = render_report(records)

    # the unused one carries no idle, so it stays plain `auto`
    assert "By trigger: auto 1 · auto idle 2 (75.0%) · auto limit 1 (0.0%)" in report


def test_unused_compactions_count_apart_and_add_only_their_overhead() -> None:
    report = render_report(
        [measured(without=100, with_=40, cached_read=7, output=1), unused(cached_read=3, output=2)]
    )

    assert "(2 compactions: 1 measured, 1 unused)" in report
    assert "Saved: 60 tokens (60.0%)" in report
    assert "10 cached read, 3 output" in report


def test_percentage_has_one_decimal_and_can_be_negative() -> None:
    report = render_report([measured(without=300, with_=400, cached_read=0, output=0)])

    assert "Saved: -100 tokens (-33.3%)" in report


def test_without_a_denominator_there_is_no_percentage() -> None:
    report = render_report([unused(), unused(trigger="manual")])

    assert "Saved: 0 tokens\n" in report
    assert "%" not in report
    assert "By trigger: auto 1 · manual 1" in report


def test_unknown_records_are_only_counted() -> None:
    unknown = replace(
        BASE, status=SavingsStatus.UNKNOWN, counterfactual_uncached=999, compaction_output=999
    )

    report = render_report([unknown, unknown])

    assert "(0 compactions: 0 measured, 0 unused)" in report
    assert "999" not in report
    assert "unknown cache TTL or metadata): 2" in report


def test_empty_ledger_says_no_compactions_were_found() -> None:
    report = render_report([])

    assert "no compactions found" in report.lower()
    assert "Compaction savings" not in report
