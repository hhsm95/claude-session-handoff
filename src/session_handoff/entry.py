"""Hook orchestration: the `/handoff` pipeline behind the `UserPromptSubmit` hook.

`run_hook` is the whole pipeline with injectable dependencies; `main` only binds it to the real
stdin, stdout, clock and tools.
"""

import functools
import json
import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from session_handoff.clipboard import copy_to_clipboard
from session_handoff.command import HandoffCommand, parse_command
from session_handoff.constants import (
    CHARS_PER_TOKEN,
    CLIPBOARD_NOTE,
    COLD_READY_MESSAGE_TEMPLATE,
    ERROR_BLOCK_MESSAGE_TEMPLATE,
    HANDOFFS_DIR,
    HOOK_INPUT_MISSING_FIELD_TEMPLATE,
    HOOK_INPUT_NOT_OBJECT_MESSAGE,
    KEY_FILES_MAX,
    LOG_FILE_NAME,
    MODEL_DOCUMENT_MAX_CHARS,
    RESUME_LINE_TEMPLATE,
    SECTION_SEPARATOR,
    UNKNOWN_IDLE_PLACEHOLDER,
    WARM_BLOCK_INTRO,
    WARM_CLOSING_TEMPLATE,
    WARM_CONTEXT_MAX_CHARS,
    WARM_FOCUS_MAX_CHARS,
    WARM_FORMAT_HEADING,
    WARM_SECTIONS_END,
    WARM_SECTIONS_INTRO,
    WARM_SECTIONS_START,
    WARM_TARGET_TEMPLATE,
)
from session_handoff.digest import build_digest, collect_file_activity, keep_start
from session_handoff.git_info import collect_git_info
from session_handoff.haiku import run_haiku
from session_handoff.handoff_doc import (
    HandoffExtras,
    assemble_handoff,
    build_haiku_input,
    build_system_prompt,
    format_instructions,
    render_deterministic_sections,
    validate_model_answer,
)
from session_handoff.hook_output import context_block, emit_block, emit_context
from session_handoff.mode import Mode, ModeDecision, decide_mode_safely
from session_handoff.storage import (
    HandoffPaths,
    apply_retention,
    ensure_private_dir,
    handoff_paths,
    open_private_file,
    write_digest,
    write_handoff,
)
from session_handoff.transcript import Transcript, read_transcript

_LOGGER = logging.getLogger("session_handoff")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


@dataclass(frozen=True)
class HookDependencies:
    now: Callable[[], datetime]
    base_dir: Path | None
    run_haiku: Callable[[str, str], str]
    copy_to_clipboard: Callable[[str], bool]


@dataclass(frozen=True)
class _HookInput:
    prompt: str
    session_id: str
    transcript_path: Path
    cwd: Path


@dataclass(frozen=True)
class _Prepared:
    """What both modes need: the saved digest and the deterministic parts of the handoff."""

    paths: HandoffPaths
    digest: str
    extras: HandoffExtras


def main(raw_input: str) -> None:
    """Run the hook on the raw stdin text that `hooks/run.py` already read."""
    dependencies = HookDependencies(
        now=lambda: datetime.now(UTC),
        base_dir=None,
        run_haiku=run_haiku,
        copy_to_clipboard=copy_to_clipboard,
    )
    run_hook(raw_input, sys.stdout, dependencies)


def run_hook(raw_input: str, stdout: TextIO, dependencies: HookDependencies) -> None:
    """Answer one hook call. Prompts that are not `/handoff` get no output at all.

    Whatever fails after the command is recognised, including the logging setup, ends in a block.
    A failure of the block itself propagates to `hooks/run.py`, which exits 2.
    """
    prompt = _prompt_of(raw_input)
    command = parse_command(prompt) if prompt is not None else None
    if command is None:
        return
    log_location = f"{HANDOFFS_DIR}/{LOG_FILE_NAME}"
    try:
        base_dir = _base_dir(dependencies)
        log_path = base_dir / LOG_FILE_NAME
        log_location = str(log_path)
        with _file_logging(log_path):
            try:
                _deliver(json.loads(raw_input), command, dependencies, base_dir, stdout)
            except Exception:
                _LOGGER.exception("handoff failed")
                raise
    except Exception as error:  # fail safe: a failed handoff must never reach the model
        reason = ERROR_BLOCK_MESSAGE_TEMPLATE.format(
            error=str(error) or type(error).__name__, log_path=log_location
        )
        emit_block(reason, stdout)


def _prompt_of(raw_input: str) -> str | None:
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError:
        return None
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    return prompt if isinstance(prompt, str) else None


def _base_dir(dependencies: HookDependencies) -> Path:
    if dependencies.base_dir is not None:
        return dependencies.base_dir
    return Path(HANDOFFS_DIR).expanduser()


@contextmanager
def _file_logging(log_path: Path) -> Iterator[None]:
    """Log to `log_path` while the pipeline runs; without a writable file, log nowhere."""
    try:
        ensure_private_dir(log_path.parent)
        os.close(open_private_file(log_path, os.O_WRONLY | os.O_APPEND))
        handler: logging.Handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    try:
        yield
    finally:
        _LOGGER.removeHandler(handler)
        handler.close()


def _deliver(
    payload: object,
    command: HandoffCommand,
    dependencies: HookDependencies,
    base_dir: Path,
    stdout: TextIO,
) -> None:
    hook_input = _parse_input(payload)
    now = dependencies.now()
    load_transcript = functools.cache(lambda: read_transcript(hook_input.transcript_path))
    decision = decide_mode_safely(load_transcript, now, force_haiku=command.force_haiku)
    prepared = _prepare(hook_input, load_transcript(), now, base_dir)
    _LOGGER.info("mode=%s idle_minutes=%s", decision.mode.value, decision.idle_minutes)
    if decision.mode is Mode.WARM:
        _deliver_warm(command, prepared, dependencies, stdout)
    else:
        _deliver_cold(command, decision, prepared, dependencies, stdout)


def _parse_input(payload: object) -> _HookInput:
    if not isinstance(payload, dict):
        raise ValueError(HOOK_INPUT_NOT_OBJECT_MESSAGE)
    fields = {}
    for key in ("prompt", "session_id", "transcript_path", "cwd"):
        value = payload.get(key)
        if not isinstance(value, str):
            raise ValueError(HOOK_INPUT_MISSING_FIELD_TEMPLATE.format(field=key))
        fields[key] = value
    return _HookInput(
        prompt=fields["prompt"],
        session_id=fields["session_id"],
        transcript_path=Path(fields["transcript_path"]),
        cwd=Path(fields["cwd"]),
    )


def _prepare(
    hook_input: _HookInput, transcript: Transcript, now: datetime, base_dir: Path
) -> _Prepared:
    activity = collect_file_activity(transcript.entries, cwd=hook_input.cwd)
    digest = build_digest(transcript.entries, activity=activity)  # redacted and neutralized
    paths = handoff_paths(hook_input.cwd, hook_input.session_id, now, base_dir)
    write_digest(paths, digest)
    extras = HandoffExtras(
        activity=activity,
        git_text=collect_git_info(hook_input.cwd),
        digest_path=paths.digest,
    )
    return _Prepared(paths=paths, digest=digest, extras=extras)


def _deliver_cold(
    command: HandoffCommand,
    decision: ModeDecision,
    prepared: _Prepared,
    dependencies: HookDependencies,
    stdout: TextIO,
) -> None:
    system_prompt = build_system_prompt(command.focus, MODEL_DOCUMENT_MAX_CHARS)
    haiku_input = build_haiku_input(prepared.extras.git_text, prepared.digest)
    answer = dependencies.run_haiku(haiku_input, system_prompt)
    document = assemble_handoff(validate_model_answer(answer), prepared.extras)
    write_handoff(prepared.paths, document)
    _apply_retention_safely(prepared.paths.handoff.parent)
    copied = dependencies.copy_to_clipboard(_resume_line(prepared.paths))
    idle = decision.idle_minutes
    message = COLD_READY_MESSAGE_TEMPLATE.format(
        chars=len(document),
        tokens=len(document) // CHARS_PER_TOKEN,
        idle_minutes=UNKNOWN_IDLE_PLACEHOLDER if idle is None else idle,
        clipboard_note=CLIPBOARD_NOTE if copied else "",
        path=prepared.paths.handoff,
    )
    emit_block(message, stdout)


def _deliver_warm(
    command: HandoffCommand,
    prepared: _Prepared,
    dependencies: HookDependencies,
    stdout: TextIO,
) -> None:
    _apply_retention_safely(prepared.paths.handoff.parent)
    dependencies.copy_to_clipboard(_resume_line(prepared.paths))
    emit_context(_warm_block_text(command, prepared), stdout)


def _apply_retention_safely(project_dir: Path) -> None:
    """Retention is housekeeping: failing must not turn a produced handoff into a failure."""
    try:
        apply_retention(project_dir)
    except Exception:
        _LOGGER.exception("retention failed")


def _warm_block_text(command: HandoffCommand, prepared: _Prepared) -> str:
    """The warm block under the context limit: the focus is capped, then key files drop, then git.

    The limit is measured on the final text, wrapped as it is emitted.
    """
    focus = keep_start(command.focus, WARM_FOCUS_MAX_CHARS)
    for key_files_limit in range(min(len(prepared.extras.activity), KEY_FILES_MAX), -1, -1):
        text = _compose_warm_block(focus, prepared, key_files_limit)
        overflow = len(context_block(text)) - WARM_CONTEXT_MAX_CHARS
        if overflow <= 0:
            return text
    git_text = prepared.extras.git_text
    shortened = replace(
        prepared.extras, git_text=keep_start(git_text, max(0, len(git_text) - overflow))
    )
    return _compose_warm_block(focus, replace(prepared, extras=shortened), key_files_limit=0)


def _compose_warm_block(focus: str, prepared: _Prepared, key_files_limit: int) -> str:
    target = prepared.paths.handoff
    instructions = format_instructions(focus, MODEL_DOCUMENT_MAX_CHARS)
    return SECTION_SEPARATOR.join(
        [
            WARM_BLOCK_INTRO,
            WARM_TARGET_TEMPLATE.format(path=target),
            WARM_CLOSING_TEMPLATE.format(path=target),
            f"{WARM_FORMAT_HEADING}\n{instructions}",
            "\n".join(
                [
                    WARM_SECTIONS_INTRO,
                    WARM_SECTIONS_START,
                    render_deterministic_sections(prepared.extras, key_files_limit=key_files_limit),
                    WARM_SECTIONS_END,
                ]
            ),
        ]
    )


def _resume_line(paths: HandoffPaths) -> str:
    return RESUME_LINE_TEMPLATE.format(path=paths.handoff)
