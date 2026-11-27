import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import write_compacted_session
from session_handoff.backfill import backfill_ledger
from session_handoff.ledger import ledger_path, read_ledger
from session_handoff.savings import SavingsStatus
from session_handoff.storage import project_slug
from session_handoff.transcript import TimelineItem, read_timeline

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def run_backfill(
    projects: Path,
    base: Path,
    *,
    read: Callable[[Path], tuple[TimelineItem, ...]] = read_timeline,
    version: str = "9.9.9",
) -> None:
    backfill_ledger(projects, base, read_timeline=read, plugin_version=version, recorded_at=NOW)


def line_count(base: Path) -> int:
    return len(ledger_path(base).read_text(encoding="utf-8").splitlines())


def test_a_boundary_with_a_later_request_is_recorded_as_measured(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-work-app" / "abc.jsonl", boundary_uuid="b1")

    run_backfill(projects, base)

    (record,) = read_ledger(base)
    assert record.status is SavingsStatus.MEASURED
    assert (record.session_id, record.boundary_uuid) == ("s1", "b1")
    assert record.project == project_slug(Path("/work/app"))
    assert (record.counterfactual_uncached, record.actual_uncached) == (100_000, 10_000)
    assert record.saved == 90_000
    assert record.plugin_version == "9.9.9"


def test_missing_session_id_and_cwd_fall_back_to_the_file_and_folder_names(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-work-other" / "file-stem.jsonl", session_id=None, cwd=None)

    run_backfill(projects, base)

    (record,) = read_ledger(base)
    assert (record.session_id, record.project) == ("file-stem", "-work-other")


def test_only_top_level_files_are_read(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-p" / "main.jsonl", boundary_uuid="main")
    write_compacted_session(projects / "-p" / "main" / "subagents" / "agent.jsonl")
    write_compacted_session(projects / "loose.jsonl", boundary_uuid="loose")

    run_backfill(projects, base)

    assert [record.boundary_uuid for record in read_ledger(base)] == ["main"]


def test_files_without_the_boundary_text_are_never_parsed(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    plain = projects / "-p" / "plain.jsonl"
    plain.parent.mkdir(parents=True)
    plain.write_text('{"type":"user"}\n', encoding="utf-8")
    write_compacted_session(projects / "-p" / "compacted.jsonl")
    parsed: list[str] = []

    def spy(path: Path) -> tuple[TimelineItem, ...]:
        parsed.append(path.name)
        return ()

    run_backfill(projects, base, read=spy)

    assert parsed == ["compacted.jsonl"]


def test_a_second_run_over_the_same_files_adds_no_lines(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-p" / "a.jsonl", boundary_uuid="b1")
    write_compacted_session(projects / "-p" / "b.jsonl", boundary_uuid="b2", reply_tokens=None)

    run_backfill(projects, base)
    first = line_count(base)
    run_backfill(projects, base)

    assert first == 2
    assert line_count(base) == first


def test_one_key_unknown_in_one_file_and_unused_in_another_adds_nothing_after_the_first_run(
    tmp_path: Path,
) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    # `s1`/`b1` in a.jsonl lacks its token counts (unknown); in b.jsonl it is a plain unused.
    first_file = write_compacted_session(projects / "-p" / "a.jsonl", reply_tokens=None)
    write_compacted_session(projects / "-p" / "b.jsonl", reply_tokens=None)
    text = first_file.read_text(encoding="utf-8")
    first_file.write_text(text.replace('"preTokens": 100000, ', ""), encoding="utf-8")

    run_backfill(projects, base)
    first = line_count(base)
    run_backfill(projects, base)
    run_backfill(projects, base)

    assert first == 1
    assert line_count(base) == first


def test_the_backfill_keeps_the_idle_before_of_a_compacted_session(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-p" / "a.jsonl")

    run_backfill(projects, base)

    (record,) = read_ledger(base)
    assert record.idle_before_seconds == 120 * 60


def test_an_unused_record_becomes_measured_when_the_session_gets_a_request(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    transcript = projects / "-p" / "a.jsonl"
    write_compacted_session(transcript, reply_tokens=None)
    run_backfill(projects, base)
    assert [record.status for record in read_ledger(base)] == [SavingsStatus.UNUSED]

    write_compacted_session(transcript, reply_tokens=10_000)
    run_backfill(projects, base)

    (record,) = read_ledger(base)
    assert record.status is SavingsStatus.MEASURED
    assert line_count(base) == 2


def test_a_measured_record_is_never_replaced(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    transcript = projects / "-p" / "a.jsonl"
    write_compacted_session(transcript, reply_tokens=10_000)
    run_backfill(projects, base)

    write_compacted_session(transcript, reply_tokens=20_000)
    run_backfill(projects, base)

    (record,) = read_ledger(base)
    assert record.actual_uncached == 10_000
    assert line_count(base) == 1


def test_one_key_in_two_files_of_the_same_pass_keeps_the_measured_record(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-p" / "a.jsonl", reply_tokens=10_000)  # measured
    write_compacted_session(projects / "-p" / "b.jsonl", reply_tokens=None)  # unused, read later

    run_backfill(projects, base)

    (record,) = read_ledger(base)
    assert record.status is SavingsStatus.MEASURED
    assert line_count(base) == 1


def test_one_key_in_two_files_keeps_the_measured_record_whatever_the_order(tmp_path: Path) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-p" / "a.jsonl", reply_tokens=None)  # unused, read first
    write_compacted_session(projects / "-p" / "b.jsonl", reply_tokens=10_000)  # measured

    run_backfill(projects, base)

    (record,) = read_ledger(base)
    assert record.status is SavingsStatus.MEASURED
    assert line_count(base) == 1


def test_an_unreadable_file_is_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    projects, base = tmp_path / "projects", tmp_path / "handoffs"
    write_compacted_session(projects / "-p" / "bad.jsonl", boundary_uuid="bad")
    write_compacted_session(projects / "-p" / "good.jsonl", boundary_uuid="good")

    def flaky(path: Path) -> tuple[TimelineItem, ...]:
        if path.name == "bad.jsonl":
            raise OSError("permission denied")
        return read_timeline(path)

    with caplog.at_level(logging.WARNING, logger="session_handoff"):
        run_backfill(projects, base, read=flaky)

    assert [record.boundary_uuid for record in read_ledger(base)] == ["good"]
    assert "bad.jsonl" in caplog.text


def test_a_missing_projects_folder_adds_nothing(tmp_path: Path) -> None:
    run_backfill(tmp_path / "nowhere", tmp_path / "handoffs")

    assert read_ledger(tmp_path / "handoffs") == []
