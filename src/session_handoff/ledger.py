"""Append-only JSON-lines ledger of compaction measurements, folded on read.

Lines are never rewritten: a later line for the same `(session_id, boundary_uuid)` replaces an
earlier one when the ledger is read, so an `unused` boundary can later become `measured`.
"""

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from session_handoff.constants import SAVINGS_LEDGER_FILE_NAME, SAVINGS_LEDGER_VERSION
from session_handoff.savings import BoundaryMeasurement, SavingsStatus
from session_handoff.storage import ensure_private_dir, open_private_file, project_slug

_VERSION_KEY = "v"
# Lower wins when two records hold the same boundary: a measurement beats a guess.
_STATUS_STRENGTH = {
    SavingsStatus.MEASURED: 0,
    SavingsStatus.UNKNOWN: 1,
    SavingsStatus.UNUSED: 2,
}


@dataclass(frozen=True)
class LedgerRecord:
    session_id: str
    boundary_uuid: str
    project: str
    timestamp: str | None
    trigger: str | None
    status: SavingsStatus
    pre_tokens: int | None
    post_tokens: int | None
    gap_seconds: int | None
    ttl_seconds: int | None
    counterfactual_uncached: int
    actual_uncached: int
    saved: int
    compaction_cached_read: int
    compaction_output: int
    plugin_version: str
    recorded_at: str
    idle_before_seconds: int | None = None
    """Added after the first records were written: a line without it reads as `None`."""


def ledger_path(base: Path) -> Path:
    return base / SAVINGS_LEDGER_FILE_NAME


def record_from_measurement(
    measurement: BoundaryMeasurement,
    *,
    session_id: str,
    project: str,
    plugin_version: str,
    recorded_at: datetime,
) -> LedgerRecord:
    return LedgerRecord(
        session_id=session_id,
        boundary_uuid=measurement.boundary_uuid,
        project=project,
        timestamp=measurement.timestamp.isoformat() if measurement.timestamp else None,
        trigger=measurement.trigger,
        status=measurement.status,
        pre_tokens=measurement.pre_tokens,
        post_tokens=measurement.post_tokens,
        gap_seconds=measurement.gap_seconds,
        ttl_seconds=measurement.ttl_seconds,
        counterfactual_uncached=measurement.counterfactual_uncached,
        actual_uncached=measurement.actual_uncached,
        saved=measurement.saved,
        compaction_cached_read=measurement.compaction_cached_read,
        compaction_output=measurement.compaction_output,
        plugin_version=plugin_version,
        recorded_at=recorded_at.isoformat(),
        idle_before_seconds=measurement.idle_before_seconds,
    )


def records_from_measurements(
    measurements: Iterable[BoundaryMeasurement],
    *,
    fallback_session_id: str,
    fallback_project: str,
    plugin_version: str,
    recorded_at: datetime,
) -> list[LedgerRecord]:
    """One record per measurement, keyed the same way whoever measures (hook or back-fill).

    The boundary's own session id and `cwd` win; the fallbacks are the transcript's file stem
    and its project, for the boundaries that carry neither.
    """
    return [
        record_from_measurement(
            measurement,
            session_id=measurement.session_id or fallback_session_id,
            project=project_slug(Path(measurement.cwd)) if measurement.cwd else fallback_project,
            plugin_version=plugin_version,
            recorded_at=recorded_at,
        )
        for measurement in measurements
    ]


def is_recorded(record: LedgerRecord, known: Mapping[tuple[str, str], LedgerRecord]) -> bool:
    """Whether the ledger already holds this very measurement, whoever wrote it and whenever."""
    earlier = known.get((record.session_id, record.boundary_uuid))
    return (
        earlier is not None
        and replace(record, recorded_at=earlier.recorded_at, plugin_version=earlier.plugin_version)
        == earlier
    )


def append_new(base: Path, candidates: Iterable[LedgerRecord]) -> None:
    """Append what the ledger lacks from the candidates of one pass.

    The candidates are folded by key first (the strongest status wins, the first of equals), so
    the same key seen twice in a pass cannot flip between runs. Then each survivor is appended
    unless the ledger already holds that measurement, holds a stronger record, or holds a
    `measured` one (final, except that one stored without its idle gets the idle).
    """
    folded: dict[tuple[str, str], LedgerRecord] = {}
    for candidate in candidates:
        key = (candidate.session_id, candidate.boundary_uuid)
        chosen = folded.get(key)
        if chosen is None or _STATUS_STRENGTH[candidate.status] < _STATUS_STRENGTH[chosen.status]:
            folded[key] = candidate
    known = {(record.session_id, record.boundary_uuid): record for record in read_ledger(base)}
    append_records(
        base,
        [
            record
            for key, record in folded.items()
            if _is_worth_appending(record, known.get(key), known)
        ],
    )


def _is_worth_appending(
    record: LedgerRecord,
    earlier: LedgerRecord | None,
    known: Mapping[tuple[str, str], LedgerRecord],
) -> bool:
    if earlier is None:
        return True
    if is_recorded(record, known):
        return False
    if earlier.status is SavingsStatus.MEASURED:
        return (
            record.status is SavingsStatus.MEASURED
            and earlier.idle_before_seconds is None
            and record.idle_before_seconds is not None
        )
    return _STATUS_STRENGTH[record.status] <= _STATUS_STRENGTH[earlier.status]


def append_records(base: Path, records: Iterable[LedgerRecord]) -> None:
    """Append each record as one line, with a single `write` per line under `O_APPEND`.

    A ledger whose last line was cut short by a crash gets a newline first, so the new record
    does not fuse with the torn one.
    """
    lines = [_encode_line(record) for record in records]
    if not lines:
        return
    ensure_private_dir(base)
    path = ledger_path(base)
    if not _ends_with_newline(path):
        lines[0] = b"\n" + lines[0]
    descriptor = open_private_file(path, os.O_WRONLY | os.O_APPEND)
    try:
        for line in lines:
            os.write(descriptor, line)
    finally:
        os.close(descriptor)


def _ends_with_newline(path: Path) -> bool:
    """`True` for a missing or empty file too: there is no line to protect."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return True
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) == b"\n"
    except FileNotFoundError:
        return True


def read_ledger(base: Path) -> list[LedgerRecord]:
    """Records folded by `(session_id, boundary_uuid)`, last line wins; bad lines are skipped."""
    path = ledger_path(base)
    if not path.is_file():
        return []
    latest: dict[tuple[str, str], LedgerRecord] = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            record = _decode_line(raw_line)
            if record is not None:
                latest[(record.session_id, record.boundary_uuid)] = record
    return list(latest.values())


def record_to_json(record: LedgerRecord) -> dict[str, object]:
    return {_VERSION_KEY: SAVINGS_LEDGER_VERSION, **asdict(record)}


def _encode_line(record: LedgerRecord) -> bytes:
    return (json.dumps(record_to_json(record)) + "\n").encode("utf-8")


def _decode_line(raw_line: str) -> LedgerRecord | None:
    try:
        decoded = json.loads(raw_line)
    except ValueError:
        return None
    if not isinstance(decoded, dict) or decoded.get(_VERSION_KEY) != SAVINGS_LEDGER_VERSION:
        return None
    return _record_from_json(decoded)


def _record_from_json(fields: Mapping[str, object]) -> LedgerRecord | None:
    try:
        return LedgerRecord(
            session_id=_string(fields["session_id"]),
            boundary_uuid=_string(fields["boundary_uuid"]),
            project=_string(fields["project"]),
            timestamp=_optional_string(fields["timestamp"]),
            trigger=_optional_string(fields["trigger"]),
            status=SavingsStatus(_string(fields["status"])),
            pre_tokens=_optional_integer(fields["pre_tokens"]),
            post_tokens=_optional_integer(fields["post_tokens"]),
            gap_seconds=_optional_integer(fields["gap_seconds"]),
            ttl_seconds=_optional_integer(fields["ttl_seconds"]),
            counterfactual_uncached=_integer(fields["counterfactual_uncached"]),
            actual_uncached=_integer(fields["actual_uncached"]),
            saved=_integer(fields["saved"]),
            compaction_cached_read=_integer(fields["compaction_cached_read"]),
            compaction_output=_integer(fields["compaction_output"]),
            plugin_version=_string(fields["plugin_version"]),
            recorded_at=_string(fields["recorded_at"]),
            idle_before_seconds=_optional_integer(fields.get("idle_before_seconds")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected an integer")
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)
