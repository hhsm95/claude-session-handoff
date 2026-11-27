"""Pure text of the savings report, from the ledger's folded records."""

from collections import defaultdict
from collections.abc import Iterable

from session_handoff.constants import (
    IDLE_COMPACTION_MIN_IDLE_SECONDS,
    SAVINGS_AUTO_IDLE_LABEL,
    SAVINGS_AUTO_LIMIT_LABEL,
    SAVINGS_AUTO_TRIGGER,
    SAVINGS_EMPTY_MESSAGE,
    SAVINGS_ESTIMATE_NOTE,
    SAVINGS_HEADER_TEMPLATE,
    SAVINGS_NO_TRIGGER_LABEL,
    SAVINGS_OVERHEAD_TEMPLATE,
    SAVINGS_PERCENT_TEMPLATE,
    SAVINGS_SAVED_TEMPLATE,
    SAVINGS_TRIGGER_SEPARATOR,
    SAVINGS_TRIGGER_TEMPLATE,
    SAVINGS_TRIGGERS_PREFIX,
    SAVINGS_UNKNOWN_TEMPLATE,
    SAVINGS_WITH_TEMPLATE,
    SAVINGS_WITHOUT_TEMPLATE,
)
from session_handoff.ledger import LedgerRecord
from session_handoff.savings import SavingsStatus


def render_report(records: Iterable[LedgerRecord]) -> str:
    """The report over `measured` and `unused` records; `unknown` ones are only counted."""
    everything = list(records)
    if not everything:
        return SAVINGS_EMPTY_MESSAGE
    counted = [record for record in everything if record.status is not SavingsStatus.UNKNOWN]
    unknown_count = len(everything) - len(counted)
    without = sum(record.counterfactual_uncached for record in counted)
    with_compaction = sum(record.actual_uncached for record in counted)
    saved = sum(record.saved for record in counted)
    width = len(f"{max(without, with_compaction):,}")
    lines = [
        SAVINGS_HEADER_TEMPLATE.format(
            total=len(counted),
            measured=_count(counted, SavingsStatus.MEASURED),
            unused=_count(counted, SavingsStatus.UNUSED),
        ),
        SAVINGS_WITHOUT_TEMPLATE.format(tokens=f"{without:,}", width=width),
        SAVINGS_WITH_TEMPLATE.format(tokens=f"{with_compaction:,}", width=width),
        SAVINGS_SAVED_TEMPLATE.format(saved=f"{saved:,}", percent=_percent(saved, without)),
        SAVINGS_OVERHEAD_TEMPLATE.format(
            cached_read=f"{sum(record.compaction_cached_read for record in counted):,}",
            output=f"{sum(record.compaction_output for record in counted):,}",
        ),
    ]
    if counted:
        lines.append(SAVINGS_TRIGGERS_PREFIX + _triggers(counted))
    if unknown_count:
        lines.append(SAVINGS_UNKNOWN_TEMPLATE.format(count=unknown_count))
    lines.append(SAVINGS_ESTIMATE_NOTE)
    return "\n".join(lines)


def _count(records: list[LedgerRecord], status: SavingsStatus) -> int:
    return sum(record.status is status for record in records)


def _percent(saved: int, without: int) -> str:
    if without == 0:
        return ""
    return SAVINGS_PERCENT_TEMPLATE.format(value=saved / without * 100)


def _trigger_label(record: LedgerRecord) -> str:
    """The trigger, with `auto` split by the idle before it; without that figure it stays `auto`."""
    if record.trigger != SAVINGS_AUTO_TRIGGER or record.idle_before_seconds is None:
        return record.trigger or SAVINGS_NO_TRIGGER_LABEL
    if record.idle_before_seconds >= IDLE_COMPACTION_MIN_IDLE_SECONDS:
        return SAVINGS_AUTO_IDLE_LABEL
    return SAVINGS_AUTO_LIMIT_LABEL


def _triggers(records: list[LedgerRecord]) -> str:
    by_trigger: dict[str, list[LedgerRecord]] = defaultdict(list)
    for record in records:
        by_trigger[_trigger_label(record)].append(record)
    return SAVINGS_TRIGGER_SEPARATOR.join(
        SAVINGS_TRIGGER_TEMPLATE.format(
            trigger=trigger,
            count=len(group),
            percent=_percent(
                sum(record.saved for record in group),
                sum(record.counterfactual_uncached for record in group),
            ),
        )
        for trigger, group in sorted(by_trigger.items())
    )
