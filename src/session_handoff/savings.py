"""Pure rule that measures what each compaction saved, from the transcript timeline only."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from session_handoff.cache_ttl import detect_cache_ttl
from session_handoff.transcript import CompactBoundary, RequestUsage, TimelineItem


class SavingsStatus(StrEnum):
    MEASURED = "measured"
    UNUSED = "unused"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BoundaryMeasurement:
    boundary_uuid: str
    timestamp: datetime | None
    trigger: str | None
    session_id: str | None
    cwd: str | None
    status: SavingsStatus
    pre_tokens: int | None
    post_tokens: int | None
    gap_seconds: int | None
    ttl_seconds: int | None
    counterfactual_uncached: int
    actual_uncached: int
    saved: int
    compaction_cached_read: int
    """Tokens the compaction read from the cache (`preTokens`); never subtracted from `saved`."""
    compaction_output: int
    """Tokens the compaction wrote as its summary (`postTokens`); never subtracted either."""
    idle_before_seconds: int | None = None
    """Time between the last request before the boundary and the boundary itself."""


def measure_boundaries(timeline: Iterable[TimelineItem]) -> list[BoundaryMeasurement]:
    """One measurement per boundary, in file order.

    `before` is the last request preceding a boundary and `after` the first one following it,
    unless another boundary comes first: a request belongs only to the last boundary before it,
    and the earlier ones stay `unused`. The counterfactual is an estimate: what the return would
    have cost without compacting.
    """
    items = list(timeline)
    return [
        _measure(item, items[:index], items[index + 1 :])
        for index, item in enumerate(items)
        if isinstance(item, CompactBoundary)
    ]


def _measure(
    boundary: CompactBoundary, preceding: list[TimelineItem], following: list[TimelineItem]
) -> BoundaryMeasurement:
    requests_before = [item for item in preceding if isinstance(item, RequestUsage)]
    after = _first_request_before_next_boundary(following)
    idle = _gap(requests_before[-1], boundary.timestamp) if requests_before else None
    idle_before = _non_negative_seconds(idle)
    pre_tokens, post_tokens = boundary.pre_tokens, boundary.post_tokens
    if pre_tokens is None or post_tokens is None:
        return _unmeasured(boundary, SavingsStatus.UNKNOWN, idle_before)
    if after is None:
        return _unmeasured(boundary, SavingsStatus.UNUSED, idle_before)

    ttl = detect_cache_ttl(requests_before)
    gap = _gap(requests_before[-1], after.timestamp) if requests_before else None
    if ttl is None or gap is None or gap < timedelta(0):  # a negative gap is a clock skew
        return _unmeasured(boundary, SavingsStatus.UNKNOWN, idle_before)

    counterfactual = pre_tokens if gap > ttl else _uncached(requests_before[-1])
    figures = _Figures(
        gap_seconds=int(gap.total_seconds()),
        ttl_seconds=int(ttl.total_seconds()),
        counterfactual=counterfactual,
        actual=_uncached(after),
        idle_before_seconds=idle_before,
    )
    return _build(boundary, SavingsStatus.MEASURED, figures)


def _first_request_before_next_boundary(following: list[TimelineItem]) -> RequestUsage | None:
    for item in following:
        if isinstance(item, CompactBoundary):
            return None
        if isinstance(item, RequestUsage):
            return item
    return None


def _uncached(request: RequestUsage) -> int:
    return request.usage.input_tokens + request.usage.cache_creation_input_tokens


def _gap(before: RequestUsage, later: datetime | None) -> timedelta | None:
    if before.timestamp is None or later is None:
        return None
    return later - before.timestamp


def _non_negative_seconds(gap: timedelta | None) -> int | None:
    if gap is None or gap < timedelta(0):
        return None
    return int(gap.total_seconds())


def _unmeasured(
    boundary: CompactBoundary, status: SavingsStatus, idle_before_seconds: int | None
) -> BoundaryMeasurement:
    return _build(boundary, status, _Figures(idle_before_seconds=idle_before_seconds))


@dataclass(frozen=True)
class _Figures:
    gap_seconds: int | None = None
    ttl_seconds: int | None = None
    counterfactual: int = 0
    actual: int = 0
    idle_before_seconds: int | None = None


def _build(
    boundary: CompactBoundary, status: SavingsStatus, figures: _Figures
) -> BoundaryMeasurement:
    return BoundaryMeasurement(
        boundary_uuid=boundary.uuid,
        timestamp=boundary.timestamp,
        trigger=boundary.trigger,
        session_id=boundary.session_id,
        cwd=boundary.cwd,
        status=status,
        pre_tokens=boundary.pre_tokens,
        post_tokens=boundary.post_tokens,
        gap_seconds=figures.gap_seconds,
        ttl_seconds=figures.ttl_seconds,
        counterfactual_uncached=figures.counterfactual,
        actual_uncached=figures.actual,
        saved=figures.counterfactual - figures.actual,
        compaction_cached_read=boundary.pre_tokens or 0,
        compaction_output=boundary.post_tokens or 0,
        idle_before_seconds=figures.idle_before_seconds,
    )
