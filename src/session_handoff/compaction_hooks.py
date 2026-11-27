"""The `PreCompact`, `PostCompact` and `Stop` handlers: none of them may fail visibly."""

import json
from pathlib import Path
from typing import TextIO

from session_handoff.compaction import pre_compact_instructions, strip_analysis
from session_handoff.constants import (
    HOOK_FAILURE_LOG_TEMPLATE,
    LOG_FILE_NAME,
    POST_COMPACT_EVENT_NAME,
    PRE_COMPACT_EVENT_NAME,
    STOP_EVENT_NAME,
)
from session_handoff.hook_output import emit_text
from session_handoff.hook_runtime import (
    LOGGER,
    HookDependencies,
    apply_retention_safely,
    base_dir_of,
    file_logging,
    string_fields,
)
from session_handoff.ledger import append_new, records_from_measurements
from session_handoff.pending import create_pending_marker, has_pending_marker, remove_pending_marker
from session_handoff.redact import redact_secrets
from session_handoff.savings import SavingsStatus, measure_boundaries
from session_handoff.storage import compact_summary_path, project_slug, write_compact_summary


def run_pre_compact(_raw_input: str, stdout: TextIO, dependencies: HookDependencies) -> None:
    """Ask the compaction for the handoff sections. Never fails: an error leaves stdout empty."""
    try:
        emit_text(pre_compact_instructions(), stdout)
    except Exception:
        _log_hook_failure(PRE_COMPACT_EVENT_NAME, dependencies)


def run_post_compact(raw_input: str, _stdout: TextIO, dependencies: HookDependencies) -> None:
    """Keep the compaction summary as a handoff file and mark the session for measuring."""
    try:
        _record_compaction(json.loads(raw_input), dependencies)
    except Exception:
        _log_hook_failure(POST_COMPACT_EVENT_NAME, dependencies)


def _record_compaction(payload: object, dependencies: HookDependencies) -> None:
    session_id = string_fields(payload, ("session_id",))["session_id"]
    base_dir = base_dir_of(dependencies)
    try:
        _save_compact_summary(payload, session_id, dependencies, base_dir)
    finally:  # measuring the savings needs the session id alone, not the summary or the cwd
        create_pending_marker(base_dir, session_id)


def _save_compact_summary(
    payload: object, session_id: str, dependencies: HookDependencies, base_dir: Path
) -> None:
    """Write the summary under the project of `cwd`; without a `cwd` or a summary, nothing."""
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    raw_summary = payload.get("compact_summary") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not isinstance(raw_summary, str):
        return
    summary = redact_secrets(strip_analysis(raw_summary))
    if not summary:
        return
    path = compact_summary_path(Path(cwd), session_id, dependencies.now(), base_dir)
    write_compact_summary(path, summary)
    apply_retention_safely(path.parent)


def run_stop(raw_input: str, _stdout: TextIO, dependencies: HookDependencies) -> None:
    """Measure what a pending compaction saved, once the first answer after it exists.

    Without a marker for the session this is one `stat`: the transcript is never read.
    """
    try:
        payload = json.loads(raw_input)
        session_id = string_fields(payload, ("session_id",))["session_id"]
        base_dir = base_dir_of(dependencies)
        if has_pending_marker(base_dir, session_id):
            fields = string_fields(payload, ("transcript_path", "cwd"))
            _measure_pending(
                session_id,
                Path(fields["transcript_path"]),
                Path(fields["cwd"]),
                dependencies,
                base_dir,
            )
    except Exception:
        _log_hook_failure(STOP_EVENT_NAME, dependencies)


def _measure_pending(
    session_id: str,
    transcript_path: Path,
    cwd: Path,
    dependencies: HookDependencies,
    base_dir: Path,
) -> None:
    measurements = measure_boundaries(dependencies.read_timeline(transcript_path))
    recorded_at = dependencies.now()
    # Only the last boundary can still get its request; an earlier unused one is final.
    waiting = bool(measurements) and measurements[-1].status is SavingsStatus.UNUSED
    final = measurements[:-1] if waiting else measurements
    records = records_from_measurements(
        final,
        fallback_session_id=session_id,
        fallback_project=project_slug(cwd),
        plugin_version=dependencies.plugin_version,
        recorded_at=recorded_at,
    )
    append_new(base_dir, records)
    if not waiting:
        remove_pending_marker(base_dir, session_id)


def _log_hook_failure(event: str, dependencies: HookDependencies) -> None:
    """Record the exception being handled in the log file; a log that cannot be written is lost."""
    try:
        with file_logging(base_dir_of(dependencies) / LOG_FILE_NAME):
            LOGGER.exception(HOOK_FAILURE_LOG_TEMPLATE.format(event=event))
    except Exception:  # nothing may escape these hooks
        return
