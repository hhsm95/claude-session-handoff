import io
import json
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from session_handoff.constants import (
    HANDOFF_MODEL_SECTIONS,
    HOOK_FAILURE_LOG_TEMPLATE,
    LOG_FILE_NAME,
    PLUGIN_VERSION,
    PRE_COMPACT_MAX_CHARS,
    RETENTION_PAIRS_PER_PROJECT,
)
from session_handoff.entry import dispatch_hook
from session_handoff.haiku import HaikuError
from session_handoff.hook_runtime import HookDependencies
from session_handoff.ledger import read_ledger
from session_handoff.pending import has_pending_marker
from session_handoff.savings import SavingsStatus
from session_handoff.storage import project_slug
from session_handoff.transcript import CompactBoundary, RequestUsage, TimelineItem, Usage

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
SESSION_ID = "0f3a9c21-aaaa-bbbb"
TimelineReader = Callable[[Path], tuple[TimelineItem, ...]]


def unused_haiku(_digest: str, _system_prompt: str) -> str:
    raise HaikuError("must not be called")


def dependencies_for(
    tmp_path: Path, read_timeline: TimelineReader | None = None
) -> HookDependencies:
    base = HookDependencies(
        now=lambda: NOW,
        base_dir=tmp_path / "handoffs",
        run_haiku=unused_haiku,
        copy_to_clipboard=lambda _text: False,
    )
    return base if read_timeline is None else replace(base, read_timeline=read_timeline)


def fire(
    tmp_path: Path, payload: Mapping[str, object], read_timeline: TimelineReader | None = None
) -> str:
    """Dispatch one event the way `entry.main` does and return what reached stdout."""
    stdout = io.StringIO()
    dispatch_hook(json.dumps(payload), stdout, dependencies_for(tmp_path, read_timeline))
    return stdout.getvalue()


def pre_compact(tmp_path: Path, **extra: object) -> str:
    payload = {
        "hook_event_name": "PreCompact",
        "session_id": SESSION_ID,
        "transcript_path": str(tmp_path / "t.jsonl"),
        "cwd": str(tmp_path),
        "trigger": "auto",
        **extra,
    }
    return fire(tmp_path, payload)


def post_compact(tmp_path: Path, summary: object, **extra: object) -> str:
    payload: dict[str, object] = {
        "hook_event_name": "PostCompact",
        "session_id": SESSION_ID,
        "transcript_path": str(tmp_path / "t.jsonl"),
        "cwd": str(tmp_path),
        "trigger": "auto",
        **extra,
    }
    if summary is not None:
        payload["compact_summary"] = summary
    return fire(tmp_path, payload)


def summaries_in(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "handoffs").rglob("*.compact.md"))


def marker_of(tmp_path: Path, session_id: str = SESSION_ID) -> Path:
    return tmp_path / "handoffs" / "pending" / session_id


# --- PreCompact ---------------------------------------------------------------------------------


def test_an_automatic_compaction_gets_the_handoff_format_instructions(tmp_path: Path) -> None:
    output = pre_compact(tmp_path, trigger="auto")

    assert output.startswith("This summary")
    assert "# Handoff: <topic>" in output
    for section in HANDOFF_MODEL_SECTIONS:
        assert f"## {section}" in output
    assert len(output) <= PRE_COMPACT_MAX_CHARS


def test_the_instructions_replace_the_default_numbered_sections(tmp_path: Path) -> None:
    output = pre_compact(tmp_path)

    assert "instead of the default numbered sections" in output


def test_manual_compaction_with_custom_instructions_gets_the_same_output_unrepeated(
    tmp_path: Path,
) -> None:
    custom = "keep the parser details " * 1_000
    automatic = pre_compact(tmp_path, trigger="auto")

    manual = pre_compact(tmp_path, trigger="manual", custom_instructions=custom)

    assert manual == automatic
    assert "parser details" not in manual
    assert len(manual) <= PRE_COMPACT_MAX_CHARS


def test_pre_compact_output_is_plain_text_and_the_same_on_every_call(tmp_path: Path) -> None:
    first = pre_compact(tmp_path)
    second = pre_compact(tmp_path)

    assert first == second
    assert not first.lstrip().startswith("{")


def test_pre_compact_neither_reads_the_transcript_nor_writes_files(tmp_path: Path) -> None:
    def forbidden(path: Path) -> tuple[TimelineItem, ...]:
        raise AssertionError(f"PreCompact read the transcript {path}")

    payload = {
        "hook_event_name": "PreCompact",
        "session_id": SESSION_ID,
        "transcript_path": str(tmp_path / "never-read.jsonl"),
        "cwd": str(tmp_path),
        "trigger": "auto",
    }

    output = fire(tmp_path, payload, forbidden)

    assert output.startswith("This summary")
    assert not (tmp_path / "handoffs").exists()


def test_pre_compact_with_bad_input_exits_silently(tmp_path: Path) -> None:
    stdout = io.StringIO()

    dispatch_hook("{broken", stdout, dependencies_for(tmp_path))
    dispatch_hook('["PreCompact"]', stdout, dependencies_for(tmp_path))

    assert stdout.getvalue() == ""


def test_a_failure_building_the_instructions_logs_and_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode() -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("session_handoff.compaction_hooks.pre_compact_instructions", explode)

    output = pre_compact(tmp_path)

    assert output == ""
    log = (tmp_path / "handoffs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "PreCompact hook failed" in log
    assert "boom" in log


# --- PostCompact --------------------------------------------------------------------------------

SUMMARY = "# Handoff: parser\n\n## Goal\nFix it.\n"


def test_post_compact_saves_the_summary_as_a_private_compact_file(tmp_path: Path) -> None:
    output = post_compact(tmp_path, SUMMARY)

    assert output == ""
    [saved] = summaries_in(tmp_path)
    assert saved.parent == tmp_path / "handoffs" / project_slug(tmp_path)
    assert saved.name == "20260501-120000-0f3a9c21.compact.md"
    assert saved.read_text(encoding="utf-8") == SUMMARY.strip()
    if sys.platform != "win32":
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_post_compact_removes_the_leading_analysis_block(tmp_path: Path) -> None:
    raw = f"<analysis>\nthinking\n\nmore thinking\n</analysis>\n\n{SUMMARY}"

    post_compact(tmp_path, raw)

    [saved] = summaries_in(tmp_path)
    assert saved.read_text(encoding="utf-8") == SUMMARY.strip()


def test_post_compact_keeps_an_analysis_tag_that_is_not_leading(tmp_path: Path) -> None:
    raw = f"{SUMMARY}\nSee <analysis>x</analysis> in the notes."

    post_compact(tmp_path, raw)

    [saved] = summaries_in(tmp_path)
    assert "<analysis>x</analysis>" in saved.read_text(encoding="utf-8")


def test_post_compact_redacts_secrets_before_writing_the_summary(tmp_path: Path) -> None:
    raw = f"{SUMMARY}\nRun `export API_TOKEN=supersecret123` first, then ask @alice.\n"

    post_compact(tmp_path, raw)

    [saved] = summaries_in(tmp_path)
    text = saved.read_text(encoding="utf-8")
    assert "supersecret123" not in text
    assert "API_TOKEN=[REDACTED]" in text
    assert "@alice" in text  # mentions are only broken for the Haiku call, not in a saved file


@pytest.mark.parametrize("summary", ["", "   \n", "<analysis>only thinking</analysis>\n", None, 7])
def test_an_empty_summary_writes_no_file_but_still_leaves_the_marker(
    tmp_path: Path, summary: object
) -> None:
    post_compact(tmp_path, summary)

    assert summaries_in(tmp_path) == []
    assert marker_of(tmp_path).is_file()


def test_post_compact_writes_a_private_empty_marker(tmp_path: Path) -> None:
    post_compact(tmp_path, SUMMARY)

    marker = marker_of(tmp_path)
    assert marker.read_bytes() == b""
    if sys.platform != "win32":
        assert stat.S_IMODE(marker.stat().st_mode) == 0o600
        assert stat.S_IMODE(marker.parent.stat().st_mode) == 0o700


def test_a_second_post_compact_for_the_same_session_keeps_one_marker(tmp_path: Path) -> None:
    post_compact(tmp_path, SUMMARY)
    post_compact(tmp_path, SUMMARY)

    assert [path.name for path in marker_of(tmp_path).parent.iterdir()] == [SESSION_ID]


@pytest.mark.parametrize("session_id", ["../escape", "a/b", "", "x" * 65, "sp ace", "café"])
def test_an_invalid_session_id_writes_no_marker(tmp_path: Path, session_id: str) -> None:
    post_compact(tmp_path, SUMMARY, session_id=session_id)

    assert not (tmp_path / "handoffs" / "pending").exists()
    assert not (tmp_path / "handoffs" / "escape").exists()


def test_post_compact_applies_the_per_project_retention(tmp_path: Path) -> None:
    folder = tmp_path / "handoffs" / project_slug(tmp_path)
    folder.mkdir(parents=True)
    for index in range(RETENTION_PAIRS_PER_PROJECT + 3):
        (folder / f"202501{index + 1:02d}-000000-abcdefgh.md").write_text("old")

    post_compact(tmp_path, SUMMARY)

    handoffs = [path for path in folder.iterdir() if not path.name.endswith(".compact.md")]
    assert len(handoffs) == RETENTION_PAIRS_PER_PROJECT
    assert summaries_in(tmp_path)


def test_summaries_never_push_the_handoffs_out_of_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "handoffs" / project_slug(tmp_path)
    folder.mkdir(parents=True)
    for index in range(RETENTION_PAIRS_PER_PROJECT):
        (folder / f"202501{index + 1:02d}-000000-abcdefgh.md").write_text("old")
    for index in range(RETENTION_PAIRS_PER_PROJECT):
        (folder / f"202502{index + 1:02d}-000000-abcdefgh.compact.md").write_text("old")

    post_compact(tmp_path, SUMMARY)

    handoffs = [path for path in folder.iterdir() if not path.name.endswith(".compact.md")]
    assert len(handoffs) == RETENTION_PAIRS_PER_PROJECT
    assert len(summaries_in(tmp_path)) == RETENTION_PAIRS_PER_PROJECT


def test_post_compact_errors_are_logged_and_never_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("session_handoff.compaction_hooks.write_compact_summary", explode)

    output = post_compact(tmp_path, SUMMARY)

    assert output == ""
    log = (tmp_path / "handoffs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "PostCompact hook failed" in log
    assert "disk full" in log
    assert marker_of(tmp_path).is_file()  # measuring does not depend on the summary


def test_post_compact_without_cwd_saves_no_summary_but_still_leaves_the_marker(
    tmp_path: Path,
) -> None:
    output = fire(
        tmp_path,
        {"hook_event_name": "PostCompact", "session_id": SESSION_ID, "compact_summary": SUMMARY},
    )

    assert output == ""
    assert summaries_in(tmp_path) == []
    assert marker_of(tmp_path).is_file()
    log_path = tmp_path / "handoffs" / LOG_FILE_NAME
    log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    assert HOOK_FAILURE_LOG_TEMPLATE.format(event="PostCompact") not in log


def test_post_compact_with_missing_fields_logs_and_exits_silently(tmp_path: Path) -> None:
    output = fire(tmp_path, {"hook_event_name": "PostCompact", "compact_summary": SUMMARY})

    assert output == ""
    assert summaries_in(tmp_path) == []
    log = (tmp_path / "handoffs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "PostCompact hook failed" in log


# --- Stop ---------------------------------------------------------------------------------------

START = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
ONE_HOUR_BUCKET = {"ephemeral_1h_input_tokens": 1}


class TimelineSpy:
    """A timeline reader that remembers every path it was asked to read."""

    def __init__(self, *items: TimelineItem) -> None:
        self.items = items
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> tuple[TimelineItem, ...]:
        self.paths.append(path)
        return self.items


def request(minutes: float, *, input_tokens: int = 0, cache_creation: int = 0) -> RequestUsage:
    return RequestUsage(
        message_id=f"msg_{minutes}",
        timestamp=START + timedelta(minutes=minutes),
        usage=Usage(
            input_tokens=input_tokens,
            cache_creation_input_tokens=cache_creation,
            **ONE_HOUR_BUCKET,
        ),
    )


def boundary(
    uuid: str = "b1", *, minutes: float = 55, session_id: str | None = None
) -> CompactBoundary:
    return CompactBoundary(
        uuid=uuid,
        timestamp=START + timedelta(minutes=minutes),
        trigger="auto",
        pre_tokens=5000,
        post_tokens=300,
        session_id=session_id,
        cwd=None,
    )


def stop(tmp_path: Path, timeline: TimelineReader) -> str:
    payload = {
        "hook_event_name": "Stop",
        "session_id": SESSION_ID,
        "transcript_path": str(tmp_path / "t.jsonl"),
        "cwd": str(tmp_path),
    }
    return fire(tmp_path, payload, timeline)


def leave_marker(tmp_path: Path) -> None:
    post_compact(tmp_path, None)


MEASURABLE = (request(0), boundary(), request(70, input_tokens=10, cache_creation=400))
NOT_YET_USED = (request(0), boundary())


def test_stop_without_a_marker_never_reads_the_transcript_nor_writes(tmp_path: Path) -> None:
    timeline = TimelineSpy(*MEASURABLE)

    output = stop(tmp_path, timeline)

    assert output == ""
    assert timeline.paths == []
    assert not (tmp_path / "handoffs").exists()


def test_stop_with_a_marker_and_an_after_request_appends_the_ledger_and_removes_the_marker(
    tmp_path: Path,
) -> None:
    leave_marker(tmp_path)
    timeline = TimelineSpy(*MEASURABLE)

    output = stop(tmp_path, timeline)

    assert output == ""
    assert timeline.paths == [tmp_path / "t.jsonl"]
    [record] = read_ledger(tmp_path / "handoffs")
    assert record.status is SavingsStatus.MEASURED
    assert record.session_id == SESSION_ID
    assert record.boundary_uuid == "b1"
    assert record.project == project_slug(tmp_path)
    assert record.plugin_version == PLUGIN_VERSION
    assert record.recorded_at == NOW.isoformat()
    assert record.saved == record.counterfactual_uncached - record.actual_uncached
    assert not has_pending_marker(tmp_path / "handoffs", SESSION_ID)


def test_stop_with_a_marker_and_still_no_after_request_keeps_the_marker(tmp_path: Path) -> None:
    leave_marker(tmp_path)

    stop(tmp_path, TimelineSpy(*NOT_YET_USED))

    assert read_ledger(tmp_path / "handoffs") == []
    assert has_pending_marker(tmp_path / "handoffs", SESSION_ID)


def test_stop_records_the_boundaries_that_have_an_after_and_keeps_the_marker_for_the_rest(
    tmp_path: Path,
) -> None:
    leave_marker(tmp_path)
    timeline = TimelineSpy(
        request(0),
        boundary("old", minutes=50),
        request(70, input_tokens=10, cache_creation=400),
        boundary("new", minutes=80),
    )

    stop(tmp_path, timeline)

    assert [record.boundary_uuid for record in read_ledger(tmp_path / "handoffs")] == ["old"]
    assert has_pending_marker(tmp_path / "handoffs", SESSION_ID)


def test_stop_records_an_earlier_unused_boundary_and_does_not_wait_for_it_forever(
    tmp_path: Path,
) -> None:
    leave_marker(tmp_path)
    timeline = TimelineSpy(
        request(0),
        boundary("first", minutes=10),
        boundary("second", minutes=20),
        request(90, cache_creation=400),
    )

    stop(tmp_path, timeline)

    records = {record.boundary_uuid: record.status for record in read_ledger(tmp_path / "handoffs")}
    assert records == {"first": SavingsStatus.UNUSED, "second": SavingsStatus.MEASURED}
    assert not has_pending_marker(tmp_path / "handoffs", SESSION_ID)


def test_stop_does_not_rewrite_a_record_that_is_already_in_the_ledger(tmp_path: Path) -> None:
    leave_marker(tmp_path)
    stop(tmp_path, TimelineSpy(*MEASURABLE))
    leave_marker(tmp_path)

    stop(tmp_path, TimelineSpy(*MEASURABLE))

    ledger = tmp_path / "handoffs" / "savings.jsonl"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_stop_keeps_a_measured_record_final_even_if_the_transcript_later_differs(
    tmp_path: Path,
) -> None:
    leave_marker(tmp_path)
    stop(tmp_path, TimelineSpy(*MEASURABLE))
    leave_marker(tmp_path)
    changed = (request(0), boundary(), request(70, input_tokens=99, cache_creation=400))

    stop(tmp_path, TimelineSpy(*changed))

    ledger = tmp_path / "handoffs" / "savings.jsonl"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    [record] = read_ledger(tmp_path / "handoffs")
    assert record.actual_uncached == 410


def test_stop_records_the_idle_before_the_compaction(tmp_path: Path) -> None:
    leave_marker(tmp_path)

    stop(tmp_path, TimelineSpy(*MEASURABLE))

    [record] = read_ledger(tmp_path / "handoffs")
    assert record.idle_before_seconds == 55 * 60


def test_stop_uses_the_boundarys_own_session_when_the_transcript_has_one(tmp_path: Path) -> None:
    leave_marker(tmp_path)
    timeline = TimelineSpy(
        request(0), boundary(session_id="original-session"), request(70, cache_creation=400)
    )

    stop(tmp_path, timeline)

    [record] = read_ledger(tmp_path / "handoffs")
    assert record.session_id == "original-session"


def test_stop_ignores_a_marker_of_another_session(tmp_path: Path) -> None:
    post_compact(tmp_path, None, session_id="another-session")
    timeline = TimelineSpy(*MEASURABLE)

    stop(tmp_path, timeline)

    assert timeline.paths == []
    assert has_pending_marker(tmp_path / "handoffs", "another-session")


def test_stop_with_an_unreadable_transcript_logs_keeps_the_marker_and_stays_silent(
    tmp_path: Path,
) -> None:
    leave_marker(tmp_path)

    def unreadable(_path: Path) -> tuple[TimelineItem, ...]:
        raise OSError("gone")

    output = stop(tmp_path, unreadable)

    assert output == ""
    assert has_pending_marker(tmp_path / "handoffs", SESSION_ID)
    log = (tmp_path / "handoffs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "Stop hook failed" in log
    assert "gone" in log
