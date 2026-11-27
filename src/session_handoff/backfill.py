"""Fill the savings ledger from the transcripts Claude Code already keeps on disk."""

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from session_handoff.constants import (
    BACKFILL_UNREADABLE_LOG_TEMPLATE,
    COMPACT_BOUNDARY_SUBTYPE,
    TRANSCRIPT_GLOB,
)
from session_handoff.hook_runtime import LOGGER
from session_handoff.ledger import LedgerRecord, append_new, records_from_measurements
from session_handoff.savings import measure_boundaries
from session_handoff.transcript import TimelineItem

_BOUNDARY_MARKER = COMPACT_BOUNDARY_SUBTYPE.encode("ascii")


def backfill_ledger(
    projects_dir: Path,
    base_dir: Path,
    *,
    read_timeline: Callable[[Path], tuple[TimelineItem, ...]],
    plugin_version: str,
    recorded_at: datetime,
) -> None:
    """Append what the ledger lacks from `<projects_dir>/*/*.jsonl`, the main sessions only.

    A boundary is added when its key is new, or when its stored record is `unused` or `unknown`
    and the transcript now says something else. A `measured` record is final. A file that cannot
    be read is skipped and logged.
    """
    candidates = [
        record
        for transcript in _transcripts_with_boundaries(projects_dir)
        for record in _records_of(transcript, read_timeline, plugin_version, recorded_at)
    ]
    append_new(base_dir, candidates)


def _transcripts_with_boundaries(projects_dir: Path) -> Iterator[Path]:
    for transcript in sorted(projects_dir.glob(TRANSCRIPT_GLOB)):
        if _mentions_boundary(transcript):
            yield transcript


def _mentions_boundary(transcript: Path) -> bool:
    """The cheap check that spares parsing the files that never compacted."""
    try:
        return _BOUNDARY_MARKER in transcript.read_bytes()
    except OSError:
        LOGGER.warning(BACKFILL_UNREADABLE_LOG_TEMPLATE.format(path=transcript), exc_info=True)
        return False


def _records_of(
    transcript: Path,
    read_timeline: Callable[[Path], tuple[TimelineItem, ...]],
    plugin_version: str,
    recorded_at: datetime,
) -> list[LedgerRecord]:
    try:
        measurements = measure_boundaries(read_timeline(transcript))
    except (OSError, ValueError):
        LOGGER.warning(BACKFILL_UNREADABLE_LOG_TEMPLATE.format(path=transcript), exc_info=True)
        return []
    return records_from_measurements(
        measurements,
        fallback_session_id=transcript.stem,
        fallback_project=transcript.parent.name,
        plugin_version=plugin_version,
        recorded_at=recorded_at,
    )
