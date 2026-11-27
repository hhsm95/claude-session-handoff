import json
import os
import stat
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from session_handoff.ledger import (
    LedgerRecord,
    append_new,
    append_records,
    is_recorded,
    ledger_path,
    read_ledger,
    record_from_measurement,
    record_to_json,
)
from session_handoff.savings import BoundaryMeasurement, SavingsStatus
from session_handoff.storage import apply_retention

RECORDED_AT = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)


def make_record(
    boundary_uuid: str = "b1",
    session_id: str = "s1",
    *,
    status: SavingsStatus = SavingsStatus.MEASURED,
    saved: int = 4590,
    timestamp: str | None = "2026-04-01T10:00:00+00:00",
    pre_tokens: int | None = 5000,
    gap_seconds: int | None = 4200,
    idle_before_seconds: int | None = None,
) -> LedgerRecord:
    return LedgerRecord(
        session_id=session_id,
        boundary_uuid=boundary_uuid,
        project="-work-app",
        timestamp=timestamp,
        trigger="auto",
        status=status,
        pre_tokens=pre_tokens,
        post_tokens=300,
        gap_seconds=gap_seconds,
        ttl_seconds=3600,
        counterfactual_uncached=5000,
        actual_uncached=410,
        saved=saved,
        compaction_cached_read=5000,
        compaction_output=300,
        plugin_version="9.9.9",
        recorded_at=RECORDED_AT.isoformat(),
        idle_before_seconds=idle_before_seconds,
    )


def lines_of(base: Path) -> list[str]:
    return ledger_path(base).read_text(encoding="utf-8").splitlines()


def test_ledger_lives_in_the_base_folder(tmp_path: Path) -> None:
    assert ledger_path(tmp_path).parent == tmp_path


def test_append_writes_one_json_line_per_record(tmp_path: Path) -> None:
    append_records(tmp_path, [make_record("b1"), make_record("b2")])

    lines = lines_of(tmp_path)
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["v"] == 1
    assert first["boundary_uuid"] == "b1"
    assert first["status"] == "measured"
    assert first["saved"] == 4590
    assert first["plugin_version"] == "9.9.9"


def test_append_adds_to_what_is_already_there(tmp_path: Path) -> None:
    append_records(tmp_path / "nested", [make_record("b1")])
    append_records(tmp_path / "nested", [make_record("b2")])

    assert len(lines_of(tmp_path / "nested")) == 2


def test_appending_nothing_creates_no_file(tmp_path: Path) -> None:
    append_records(tmp_path, [])

    assert not ledger_path(tmp_path).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes do not apply on Windows")
def test_ledger_and_its_folder_are_owner_only(tmp_path: Path) -> None:
    base = tmp_path / "handoffs"

    append_records(base, [make_record()])

    assert stat.S_IMODE(ledger_path(base).stat().st_mode) == 0o600
    assert stat.S_IMODE(base.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes do not apply on Windows")
def test_a_ledger_that_was_world_readable_is_tightened(tmp_path: Path) -> None:
    ledger_path(tmp_path).write_text("", encoding="utf-8")
    ledger_path(tmp_path).chmod(0o644)

    append_records(tmp_path, [make_record()])

    assert stat.S_IMODE(ledger_path(tmp_path).stat().st_mode) == 0o600


def test_each_record_is_one_write_call_so_lines_stay_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[bytes] = []
    real_write = os.write

    def spy(descriptor: int, data: bytes) -> int:
        writes.append(bytes(data))
        return real_write(descriptor, data)

    monkeypatch.setattr(os, "write", spy)

    append_records(tmp_path, [make_record("b1"), make_record("b2")])

    assert len(writes) == 2
    assert all(chunk.endswith(b"\n") and chunk.count(b"\n") == 1 for chunk in writes)


def test_read_round_trips_a_record(tmp_path: Path) -> None:
    record = make_record(pre_tokens=None, gap_seconds=None, timestamp=None)

    append_records(tmp_path, [record])

    assert read_ledger(tmp_path) == [record]


def test_read_of_a_missing_ledger_is_empty(tmp_path: Path) -> None:
    assert read_ledger(tmp_path) == []


def test_last_line_wins_for_the_same_session_and_boundary(tmp_path: Path) -> None:
    unused = make_record("b1", status=SavingsStatus.UNUSED, saved=0)
    measured = make_record("b1", status=SavingsStatus.MEASURED, saved=77)

    append_records(tmp_path, [unused])
    append_records(tmp_path, [make_record("b2"), measured])

    folded = read_ledger(tmp_path)

    assert [(r.boundary_uuid, r.status, r.saved) for r in folded] == [
        ("b1", SavingsStatus.MEASURED, 77),
        ("b2", SavingsStatus.MEASURED, 4590),
    ]


def test_same_boundary_uuid_in_two_sessions_are_two_records(tmp_path: Path) -> None:
    append_records(tmp_path, [make_record("b1", "s1"), make_record("b1", "s2")])

    assert len(read_ledger(tmp_path)) == 2


def test_unreadable_lines_are_skipped(tmp_path: Path) -> None:
    good = _line_of(make_record("ok"))
    wrong_status = json.dumps({**json.loads(_line_of(make_record("x"))), "status": "weird"})
    wrong_type = json.dumps({**json.loads(_line_of(make_record("y"))), "saved": "lots"})
    missing_key = json.dumps(
        {k: v for k, v in json.loads(_line_of(make_record("z"))).items() if k != "session_id"}
    )
    future_version = json.dumps({**json.loads(_line_of(make_record("w"))), "v": 2})
    bool_as_int = json.dumps({**json.loads(_line_of(make_record("t"))), "saved": True})
    ledger_path(tmp_path).write_text(
        "\n".join(
            ["not json", "[1, 2]", "", wrong_status, wrong_type, missing_key, future_version]
            + [bool_as_int, good]
        )
        + "\n",
        encoding="utf-8",
    )

    assert [record.boundary_uuid for record in read_ledger(tmp_path)] == ["ok"]


def test_a_torn_last_line_does_not_hide_the_whole_lines_before_it(tmp_path: Path) -> None:
    append_records(tmp_path, [make_record("b1")])
    with ledger_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"v": 1, "session_')

    assert [record.boundary_uuid for record in read_ledger(tmp_path)] == ["b1"]


def test_an_append_after_a_line_cut_short_by_a_crash_does_not_lose_the_new_record(
    tmp_path: Path,
) -> None:
    append_records(tmp_path, [make_record("b1")])
    with ledger_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"v": 1, "session_')  # a crash left the line without its newline

    append_records(tmp_path, [make_record("b2"), make_record("b3")])

    assert [record.boundary_uuid for record in read_ledger(tmp_path)] == ["b1", "b2", "b3"]


def test_an_append_to_a_ledger_that_ends_in_a_newline_adds_no_blank_line(tmp_path: Path) -> None:
    append_records(tmp_path, [make_record("b1")])
    append_records(tmp_path, [make_record("b2")])

    assert all(lines_of(tmp_path))
    assert len(lines_of(tmp_path)) == 2


def test_idle_before_round_trips_and_a_line_without_it_reads_as_none(tmp_path: Path) -> None:
    append_records(tmp_path, [make_record("with", idle_before_seconds=2400)])
    old_line = {
        key: value
        for key, value in json.loads(_line_of(make_record("old"))).items()
        if key != "idle_before_seconds"
    }
    with ledger_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(old_line) + "\n")

    by_uuid = {record.boundary_uuid: record for record in read_ledger(tmp_path)}

    assert by_uuid["with"].idle_before_seconds == 2400
    assert by_uuid["old"].idle_before_seconds is None


def test_a_record_that_differs_only_in_time_and_plugin_version_is_already_recorded() -> None:
    earlier = make_record()
    known = {(earlier.session_id, earlier.boundary_uuid): earlier}
    rewritten = replace(earlier, recorded_at="2099-01-01T00:00:00+00:00", plugin_version="10.0.0")

    assert is_recorded(rewritten, known)


def test_a_record_with_another_measurement_is_not_already_recorded() -> None:
    earlier = make_record()
    known = {(earlier.session_id, earlier.boundary_uuid): earlier}

    assert not is_recorded(replace(earlier, saved=earlier.saved + 1), known)
    assert not is_recorded(make_record("another"), known)


def test_record_from_measurement_adds_the_caller_supplied_fields() -> None:
    measurement = BoundaryMeasurement(
        boundary_uuid="b7",
        timestamp=datetime(2026, 4, 1, 9, 30, tzinfo=UTC),
        trigger="manual",
        session_id="ignored-by-the-record",
        cwd="/ignored",
        status=SavingsStatus.UNUSED,
        pre_tokens=800,
        post_tokens=40,
        gap_seconds=None,
        ttl_seconds=None,
        counterfactual_uncached=0,
        actual_uncached=0,
        saved=0,
        compaction_cached_read=800,
        compaction_output=40,
        idle_before_seconds=1800,
    )

    record = record_from_measurement(
        measurement,
        session_id="s5",
        project="-proj",
        plugin_version="1.2.3",
        recorded_at=RECORDED_AT,
    )

    assert record.session_id == "s5"
    assert record.project == "-proj"
    assert record.boundary_uuid == "b7"
    assert record.timestamp == "2026-04-01T09:30:00+00:00"
    assert record.status is SavingsStatus.UNUSED
    assert record.compaction_cached_read == 800
    assert record.plugin_version == "1.2.3"
    assert record.recorded_at == "2026-04-02T12:00:00+00:00"
    assert record.idle_before_seconds == 1800


def test_retention_never_touches_the_ledger_or_the_pending_markers(tmp_path: Path) -> None:
    base = tmp_path / "handoffs"
    append_records(base, [make_record()])
    pending = base / "pending"
    pending.mkdir()
    marker = pending / "s1"
    marker.write_text("x", encoding="utf-8")
    project_dir = base / "proj"
    project_dir.mkdir()
    for stem in ("20260101-000000-aaaaaaaa", "20260102-000000-bbbbbbbb"):
        (project_dir / f"{stem}.md").write_text("h", encoding="utf-8")
        (project_dir / f"{stem}.digest.md").write_text("d", encoding="utf-8")

    apply_retention(project_dir, keep=0)
    apply_retention(base, keep=0)

    assert not list(project_dir.iterdir())
    assert len(read_ledger(base)) == 1
    assert marker.exists()


def _line_of(record: LedgerRecord) -> str:
    return json.dumps(record_to_json(record))


def test_append_new_folds_the_candidates_of_a_pass_before_comparing_with_the_ledger(
    tmp_path: Path,
) -> None:
    base = tmp_path / "handoffs"
    unknown = make_record(status=SavingsStatus.UNKNOWN, saved=0)
    unused = make_record(status=SavingsStatus.UNUSED, saved=0, pre_tokens=1)

    for _ in range(3):
        append_new(base, [unknown, unused])

    assert [record.status for record in read_ledger(base)] == [SavingsStatus.UNKNOWN]
    assert len(ledger_path(base).read_text(encoding="utf-8").splitlines()) == 1


def test_append_new_never_replaces_a_stronger_record_with_a_weaker_one(tmp_path: Path) -> None:
    base = tmp_path / "handoffs"
    append_records(base, [make_record(status=SavingsStatus.UNKNOWN, saved=0)])

    append_new(base, [make_record(status=SavingsStatus.UNUSED, saved=0, pre_tokens=1)])

    assert [record.status for record in read_ledger(base)] == [SavingsStatus.UNKNOWN]


def test_append_new_refreshes_a_measured_record_that_lacked_the_idle(tmp_path: Path) -> None:
    base = tmp_path / "handoffs"
    append_records(base, [make_record(idle_before_seconds=None)])

    append_new(base, [make_record(idle_before_seconds=7200)])
    append_new(base, [make_record(idle_before_seconds=9999)])

    (record,) = read_ledger(base)
    assert record.idle_before_seconds == 7200
    assert len(ledger_path(base).read_text(encoding="utf-8").splitlines()) == 2
