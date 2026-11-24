import io
import json
import stat
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conftest import GIT_WORST_CASE_COMMANDS, VALID_HANDOFF_TEXT, TranscriptWriter
from session_handoff import digest as digest_module
from session_handoff.constants import (
    CLIPBOARD_TIMEOUT_SECONDS,
    DIGEST_FILE_SUFFIX,
    GIT_TIMEOUT_SECONDS,
    HAIKU_TIMEOUT_SECONDS,
    HOOK_INACTIVE_MESSAGE,
    LOG_FILE_NAME,
    MENTION_GUARD,
    NO_GIT_MESSAGE,
    RETENTION_PAIRS_PER_PROJECT,
    TRUNCATION_ELLIPSIS,
    WARM_CONTEXT_MAX_CHARS,
    WARM_FOCUS_MAX_CHARS,
)
from session_handoff.digest import FileActivity
from session_handoff.entry import HookDependencies, run_hook
from session_handoff.haiku import HaikuError
from session_handoff.storage import project_slug
from session_handoff.transcript import Entry

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(hours=5)
JUST_NOW = NOW - timedelta(minutes=1)
# Claude Code spills hook output over this size into a file instead of the context.
CLAUDE_CODE_CONTEXT_LIMIT_CHARS = 10_000


class Recorder:
    """Fakes for Haiku and the clipboard that remember how they were called."""

    def __init__(self, *, answer: str = VALID_HANDOFF_TEXT, clipboard_works: bool = True) -> None:
        self.answer = answer
        self.clipboard_works = clipboard_works
        self.haiku_calls: list[tuple[str, str]] = []
        self.copied: list[str] = []

    def haiku(self, digest: str, system_prompt: str) -> str:
        self.haiku_calls.append((digest, system_prompt))
        return self.answer

    def clipboard(self, text: str) -> bool:
        self.copied.append(text)
        return self.clipboard_works


def failing_haiku(_digest: str, _system_prompt: str) -> str:
    raise HaikuError("login expired")


def run(
    tmp_path: Path,
    prompt: str,
    transcript: Path | None,
    recorder: Recorder,
    *,
    haiku: Callable[[str, str], str] | None = None,
) -> dict[str, object]:
    stdin = json.dumps(
        {
            "prompt": prompt,
            "session_id": "0f3a9c21-aaaa",
            "transcript_path": str(transcript) if transcript else "",
            "cwd": str(tmp_path),
        }
    )
    stdout = io.StringIO()
    dependencies = HookDependencies(
        now=lambda: NOW,
        base_dir=tmp_path / "handoffs",
        run_haiku=haiku or recorder.haiku,
        copy_to_clipboard=recorder.clipboard,
    )
    run_hook(stdin, stdout, dependencies)
    output = stdout.getvalue()
    parsed: dict[str, object] = json.loads(output) if output else {}
    return parsed


def files_in(tmp_path: Path, pattern: str) -> list[Path]:
    return sorted((tmp_path / "handoffs").rglob(pattern))


def handoffs_in(tmp_path: Path) -> list[Path]:
    """Handoff documents only: every `.md` file that is not a digest."""
    return [
        path for path in files_in(tmp_path, "*.md") if not path.name.endswith(DIGEST_FILE_SUFFIX)
    ]


def resume_line_of(reason: str) -> str:
    return reason.splitlines()[-1]


# --- not the command ----------------------------------------------------------------------------


def test_a_prompt_that_is_not_the_command_produces_no_output_and_reads_nothing(
    tmp_path: Path,
) -> None:
    recorder = Recorder()

    output = run(tmp_path, "hello", tmp_path / "never-read.jsonl", recorder)

    assert output == {}
    assert recorder.haiku_calls == []
    assert not (tmp_path / "handoffs").exists()


def test_invalid_stdin_produces_no_output(tmp_path: Path) -> None:
    stdout = io.StringIO()
    dependencies = HookDependencies(
        now=lambda: NOW,
        base_dir=tmp_path,
        run_haiku=Recorder().haiku,
        copy_to_clipboard=Recorder().clipboard,
    )

    run_hook("{not json", stdout, dependencies)

    assert stdout.getvalue() == ""


# --- cold ---------------------------------------------------------------------------------------


def test_cold_success_blocks_with_the_resume_line_of_an_existing_handoff(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    output = run(tmp_path, "/session-handoff:handoff", transcript, recorder)

    assert output["decision"] == "block"
    reason = str(output["reason"])
    resume_line = resume_line_of(reason)
    assert resume_line.startswith("@")
    assert Path(resume_line.removeprefix("@")).is_absolute()
    handoff = Path(resume_line.removeprefix("@"))
    assert handoff.is_file()
    assert handoff.read_text(encoding="utf-8").startswith("# Handoff: parser bug")
    assert len(files_in(tmp_path, "*.digest.md")) == 1


def test_cold_message_reports_size_tokens_idle_and_clipboard(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, recorder)

    reason = str(output["reason"])
    handoff = Path(resume_line_of(reason).removeprefix("@"))
    chars = len(handoff.read_text(encoding="utf-8"))
    assert f"{chars} chars, ~{chars // 4} tokens; idle 300 min" in reason
    assert "already in your clipboard" in reason
    assert recorder.copied == [resume_line_of(reason)]


def test_cold_message_without_a_clipboard_does_not_mention_it(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder(clipboard_works=False)
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, recorder)

    assert output["decision"] == "block"
    assert "clipboard" not in str(output["reason"])
    resume_line = resume_line_of(str(output["reason"]))
    assert Path(resume_line.removeprefix("@")).is_absolute()


def test_cold_mode_sends_the_focus_to_haiku(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    run(tmp_path, "/handoff only the parser", transcript, recorder)

    ((digest, system_prompt),) = recorder.haiku_calls
    assert "Please fix a.py" in digest
    assert "User priority: only the parser" in system_prompt


def test_mentions_are_neutralized_in_the_digest_sent_to_haiku_and_in_the_saved_one(
    tmp_path: Path,
) -> None:
    prompt = {
        "type": "user",
        "timestamp": LONG_AGO.isoformat(),
        "message": {"role": "user", "content": "compare with @/etc/hosts please"},
    }
    recorder = Recorder()

    run(tmp_path, "/handoff", write_raw_transcript(tmp_path, [prompt]), recorder)

    ((sent, _system_prompt),) = recorder.haiku_calls
    (saved_path,) = files_in(tmp_path, "*.digest.md")
    for digest in (sent, saved_path.read_text(encoding="utf-8")):
        assert f"@{MENTION_GUARD}/etc/hosts" in digest
        assert "@/etc/hosts" not in digest


def test_haiku_input_starts_with_the_repository_state_followed_by_the_digest(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    run(tmp_path, "/handoff", transcript, recorder)

    ((sent, _system_prompt),) = recorder.haiku_calls
    heading = "## Repository state (authoritative, generated by code)"
    assert sent.startswith(heading)
    assert NO_GIT_MESSAGE in sent[: sent.index("Please fix a.py")]
    assert sent.index(heading) < sent.index("Please fix a.py")


def test_the_repository_state_sent_to_haiku_is_redacted_and_neutralized(
    tmp_path: Path, write_transcript: TranscriptWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_text = "feature/x\nlast: use @/etc/hosts with TOKEN=abc123secret"
    monkeypatch.setattr("session_handoff.entry.collect_git_info", lambda _cwd: git_text)
    recorder = Recorder()
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    run(tmp_path, "/handoff", transcript, recorder)

    ((sent, _system_prompt),) = recorder.haiku_calls
    assert "feature/x" in sent
    assert "@/etc/hosts" not in sent
    assert "abc123secret" not in sent


def test_haiku_flag_with_a_live_cache_still_goes_cold(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, "/handoff --haiku", transcript, recorder)

    assert output["decision"] == "block"
    assert len(recorder.haiku_calls) == 1


def test_a_transcript_with_no_cache_writes_goes_cold_with_unknown_ttl(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(JUST_NOW, cache_1h=False)

    output = run(tmp_path, "/handoff", transcript, recorder)

    assert output["decision"] == "block"
    assert len(recorder.haiku_calls) == 1


# --- failures -----------------------------------------------------------------------------------


def test_haiku_failure_blocks_with_the_error_and_saves_no_handoff(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, Recorder(), haiku=failing_haiku)

    assert output["decision"] == "block"
    assert "Handoff failed: login expired" in str(output["reason"])
    assert str(tmp_path / "handoffs" / LOG_FILE_NAME) in str(output["reason"])
    assert handoffs_in(tmp_path) == []


def test_haiku_failure_is_logged_to_the_log_file(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    run(tmp_path, "/handoff", transcript, Recorder(), haiku=failing_haiku)

    log = (tmp_path / "handoffs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "HaikuError" in log
    assert "login expired" in log


def test_a_failure_while_setting_up_logging_still_blocks(
    tmp_path: Path, write_transcript: TranscriptWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_ensure_private_dir(_path: Path) -> None:
        raise RuntimeError("no home directory")

    monkeypatch.setattr("session_handoff.entry.ensure_private_dir", broken_ensure_private_dir)
    recorder = Recorder()
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, recorder)

    assert output["decision"] == "block"
    assert "no home directory" in str(output["reason"])
    assert recorder.haiku_calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_log_file_is_readable_only_by_its_owner(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    run(tmp_path, "/handoff", transcript, Recorder(), haiku=failing_haiku)

    log_path = tmp_path / "handoffs" / LOG_FILE_NAME
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700


def test_an_invalid_haiku_answer_blocks_keeps_the_digest_and_saves_no_handoff(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder(answer="just chatting, no document")
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, recorder)

    assert output["decision"] == "block"
    assert "Handoff failed" in str(output["reason"])
    assert len(files_in(tmp_path, "*.digest.md")) == 1
    assert handoffs_in(tmp_path) == []
    assert recorder.copied == []


def write_raw_transcript(tmp_path: Path, records: list[object]) -> Path:
    path = tmp_path / "raw.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def thinking_only_request() -> dict[str, object]:
    """A recent request with a live 1h cache whose only content is thinking, which is dropped."""
    return {
        "type": "assistant",
        "timestamp": JUST_NOW.isoformat(),
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "model": "claude-opus-4-1",
            "content": [{"type": "thinking", "thinking": "private"}],
            "usage": {"cache_creation": {"ephemeral_1h_input_tokens": 100}},
        },
    }


@pytest.mark.parametrize(
    "records",
    [
        [{"type": "future-kind", "payload": 1}, {"unexpected": True}],
        [thinking_only_request()],
    ],
    ids=["unknown-format-lines", "only-dropped-content-with-a-live-cache"],
)
def test_a_transcript_with_no_conversation_blocks_before_any_model_or_warm_block(
    tmp_path: Path, records: list[object]
) -> None:
    recorder = Recorder()

    output = run(tmp_path, "/handoff", write_raw_transcript(tmp_path, records), recorder)

    assert output["decision"] == "block"
    assert "Handoff failed" in str(output["reason"])
    assert recorder.haiku_calls == []
    assert files_in(tmp_path, "*.digest.md") == []


def test_an_unreadable_transcript_blocks_with_the_error(tmp_path: Path) -> None:
    output = run(tmp_path, "/handoff", tmp_path / "missing.jsonl", Recorder())

    assert output["decision"] == "block"
    assert "Handoff failed" in str(output["reason"])
    assert handoffs_in(tmp_path) == []


# --- warm ---------------------------------------------------------------------------------------


def test_warm_mode_does_not_block_and_calls_no_model(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, recorder)

    assert "decision" not in output
    assert recorder.haiku_calls == []
    assert handoffs_in(tmp_path) == []


def test_warm_context_has_the_target_path_format_and_verbatim_sections(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    recorder = Recorder()
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, "/handoff only the parser", transcript, recorder)

    specific = output["hookSpecificOutput"]
    assert isinstance(specific, dict)
    context = str(specific["additionalContext"])
    digest = files_in(tmp_path, "*.digest.md")[0]
    target = digest.with_name(digest.name.removesuffix(".digest.md") + ".md")
    assert context.startswith("<session-handoff>")
    assert f"Target file (absolute path): {target}" in context
    assert "## Goal" in context
    assert "User priority: only the parser" in context
    assert "## Key files" in context
    assert "## Branch / commits" in context
    assert f"Full detail (read only if needed): {digest}" in context
    assert f"@{target}" in context
    assert recorder.copied == [f"@{target}"]


def warm_context_of(output: dict[str, object]) -> str:
    specific = output["hookSpecificOutput"]
    assert isinstance(specific, dict)
    return str(specific["additionalContext"])


def test_warm_block_puts_the_closing_instruction_before_the_format_and_the_verbatim_text(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, Recorder())

    context = warm_context_of(output)
    closing = context.index("Write the file with the Write tool")
    assert closing < context.index("## Goal")
    assert closing < context.index("--- BEGIN VERBATIM ---")


def test_warm_block_tells_the_model_not_to_write_the_two_marker_lines(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, Recorder())

    context = warm_context_of(output)
    assert "without the two marker lines" in context
    assert context.index("without the two marker lines") < context.index("--- BEGIN VERBATIM ---")


def test_warm_block_asks_for_the_resume_path_alone_on_its_own_line_without_backticks(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, Recorder())

    context = warm_context_of(output)
    digest = files_in(tmp_path, "*.digest.md")[0]
    target = digest.with_name(digest.name.removesuffix(".digest.md") + ".md")
    lines = context.splitlines()
    assert lines[lines.index(f"@{target}") - 1] == (
        "Handoff ready. To resume: /clear, then paste this line:"
    )
    assert f"`@{target}`" not in context


def test_warm_block_stays_under_the_context_limit_however_many_key_files_there_are(
    tmp_path: Path,
) -> None:
    long_paths = [f"{tmp_path}/{'deep/' * 40}module_{index}.py" for index in range(80)]
    request = {
        "type": "assistant",
        "timestamp": JUST_NOW.isoformat(),
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "model": "claude-opus-4-1",
            "content": [
                {"type": "tool_use", "id": f"t{index}", "name": "Read", "input": {"file_path": p}}
                for index, p in enumerate(long_paths)
            ],
            "usage": {"cache_creation": {"ephemeral_1h_input_tokens": 100}},
        },
    }
    transcript = write_raw_transcript(tmp_path, [request])

    output = run(tmp_path, "/handoff", transcript, Recorder())

    context = warm_context_of(output)
    assert len(context) < CLAUDE_CODE_CONTEXT_LIMIT_CHARS
    assert "BEGIN VERBATIM" in context
    assert "Handoff ready" in context


def test_warm_mode_saves_the_digest(tmp_path: Path, write_transcript: TranscriptWriter) -> None:
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    run(tmp_path, "/handoff", transcript, Recorder())

    (digest,) = files_in(tmp_path, "*.digest.md")
    assert "Please fix a.py" in digest.read_text(encoding="utf-8")


def bash_only_transcript(tmp_path: Path, when: datetime, command: str) -> Path:
    """A one-request transcript whose only tool call is `command` through `Bash`."""
    request = {
        "type": "assistant",
        "timestamp": when.isoformat(),
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "model": "claude-opus-4-1",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": command}}
            ],
            "usage": {"cache_creation": {"ephemeral_1h_input_tokens": 100}},
        },
    }
    return write_raw_transcript(tmp_path, [request])


def test_cold_handoff_lists_the_files_a_bash_only_session_touched_as_bash_refs(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    transcript = bash_only_transcript(tmp_path, LONG_AGO, "cat a.py")

    output = run(tmp_path, "/handoff", transcript, Recorder())

    handoff = Path(resume_line_of(str(output["reason"])).removeprefix("@"))
    assert "`a.py`: 0 reads, 0 edits, 0 writes, 1 bash refs" in handoff.read_text(encoding="utf-8")


def test_warm_block_lists_the_files_a_bash_only_session_touched_as_bash_refs(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    transcript = bash_only_transcript(tmp_path, JUST_NOW, "cat a.py")

    output = run(tmp_path, "/handoff", transcript, Recorder())

    context = warm_context_of(output)
    assert "`a.py`: 0 reads, 0 edits, 0 writes, 1 bash refs" in context


# --- retention ----------------------------------------------------------------------------------


def seed_old_pairs(tmp_path: Path, count: int) -> list[str]:
    """`count` handoff/digest pairs older than any run, in the folder the hook will use."""
    folder = tmp_path / "handoffs" / project_slug(tmp_path)
    folder.mkdir(parents=True)
    stems = [f"20260101-0000{index:02d}-seed{index:04d}" for index in range(count)]
    for stem in stems:
        (folder / f"{stem}.md").write_text("old handoff", encoding="utf-8")
        (folder / f"{stem}.digest.md").write_text("old digest", encoding="utf-8")
    return stems


def stems_in(folder: Path) -> set[str]:
    return {path.name.split(".")[0] for path in folder.iterdir()}


def test_a_cold_success_keeps_the_newest_ten_pairs_and_drops_the_oldest(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    seeded = seed_old_pairs(tmp_path, RETENTION_PAIRS_PER_PROJECT)
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, Recorder())

    new_stem = Path(resume_line_of(str(output["reason"])).removeprefix("@")).name.split(".")[0]
    folder = tmp_path / "handoffs" / project_slug(tmp_path)
    assert stems_in(folder) == {*seeded[1:], new_stem}
    assert len(list(folder.glob("*.md"))) == 2 * RETENTION_PAIRS_PER_PROJECT


def test_a_warm_run_leaves_at_most_ten_pairs_including_the_new_digest(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    seeded = seed_old_pairs(tmp_path, RETENTION_PAIRS_PER_PROJECT)
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    run(tmp_path, "/handoff", transcript, Recorder())

    folder = tmp_path / "handoffs" / project_slug(tmp_path)
    (new_digest,) = files_in(tmp_path, "*.digest.md")[-1:]
    assert len(stems_in(folder)) == RETENTION_PAIRS_PER_PROJECT
    assert new_digest.name.split(".")[0] in stems_in(folder)
    assert seeded[0] not in stems_in(folder)


@pytest.mark.parametrize(
    ("last_activity", "delivered_as"), [(LONG_AGO, "block"), (JUST_NOW, "context")]
)
def test_a_retention_failure_never_turns_a_produced_handoff_into_a_failure(
    tmp_path: Path,
    write_transcript: TranscriptWriter,
    monkeypatch: pytest.MonkeyPatch,
    last_activity: datetime,
    delivered_as: str,
) -> None:
    def failing_retention(_folder: Path) -> None:
        raise PermissionError("cannot delete")

    monkeypatch.setattr("session_handoff.entry.apply_retention", failing_retention)
    transcript = write_transcript(last_activity, cache_1h=True)

    output = run(tmp_path, "/handoff", transcript, Recorder())

    if delivered_as == "block":
        assert "Handoff ready" in str(output["reason"])
    else:
        assert "decision" not in output
    log = (tmp_path / "handoffs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "cannot delete" in log


def test_the_slowest_pipeline_fits_well_inside_the_hook_timeout() -> None:
    """If the hook is killed by its timeout the prompt reaches the model, so keep a margin."""
    hooks_file = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"
    registered = json.loads(hooks_file.read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"]
    hook_timeout = registered[0]["hooks"][0]["timeout"]
    margin_seconds = 10

    worst_case = (
        HAIKU_TIMEOUT_SECONDS
        + len(GIT_WORST_CASE_COMMANDS) * GIT_TIMEOUT_SECONDS
        + CLIPBOARD_TIMEOUT_SECONDS
        + margin_seconds
    )

    assert worst_case < hook_timeout


def test_the_command_body_tells_the_model_the_exact_inactive_hook_sentence() -> None:
    command_file = Path(__file__).resolve().parent.parent / "commands" / "handoff.md"

    assert HOOK_INACTIVE_MESSAGE in command_file.read_text(encoding="utf-8")


def test_the_plugin_registers_only_the_user_prompt_submit_hook() -> None:
    hooks_file = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"

    registered = json.loads(hooks_file.read_text(encoding="utf-8"))["hooks"]

    assert list(registered) == ["UserPromptSubmit"]


def test_the_file_activity_is_computed_once_per_hook_call(
    tmp_path: Path, write_transcript: TranscriptWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path | None] = []
    real_collect = digest_module.collect_file_activity

    def counting_collect(
        entries: Sequence[Entry], *, cwd: Path | None = None
    ) -> tuple[FileActivity, ...]:
        calls.append(cwd)
        return real_collect(entries, cwd=cwd)

    monkeypatch.setattr("session_handoff.digest.collect_file_activity", counting_collect)
    monkeypatch.setattr("session_handoff.entry.collect_file_activity", counting_collect)
    transcript = write_transcript(LONG_AGO, cache_1h=True)

    run(tmp_path, "/handoff", transcript, Recorder())

    assert len(calls) == 1


def test_a_huge_focus_cannot_push_the_warm_block_over_the_context_limit(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(JUST_NOW, cache_1h=True)
    focus = " ".join(f"word{index}" for index in range(3000))

    output = run(tmp_path, f"/handoff {focus}", transcript, Recorder())

    context = warm_context_of(output)
    assert len(context) <= WARM_CONTEXT_MAX_CHARS
    assert "Write the file with the Write tool" in context
    assert "User priority: word0 word1" in context
    assert "word2999" not in context


def test_a_focus_is_cut_at_exactly_the_warm_focus_limit(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    transcript = write_transcript(JUST_NOW, cache_1h=True)

    output = run(tmp_path, f"/handoff {'x' * 5000}", transcript, Recorder())

    # The ellipsis counts toward the limit, so the shown focus is exactly WARM_FOCUS_MAX_CHARS long.
    kept = "x" * (WARM_FOCUS_MAX_CHARS - len(TRUNCATION_ELLIPSIS))
    context = warm_context_of(output)
    assert kept + TRUNCATION_ELLIPSIS in context
    assert "x" * (WARM_FOCUS_MAX_CHARS - len(TRUNCATION_ELLIPSIS) + 1) not in context


def warm_context_with_git_text(
    tmp_path: Path,
    write_transcript: TranscriptWriter,
    monkeypatch: pytest.MonkeyPatch,
    git_text: str,
) -> str:
    monkeypatch.setattr("session_handoff.entry.collect_git_info", lambda _cwd: git_text)
    transcript = write_transcript(JUST_NOW, cache_1h=True)
    return warm_context_of(run(tmp_path, "/handoff", transcript, Recorder()))


def test_a_warm_block_exactly_at_the_limit_is_kept_whole(
    tmp_path: Path, write_transcript: TranscriptWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_git = warm_context_with_git_text(tmp_path, write_transcript, monkeypatch, "")
    git_text = "g" * (WARM_CONTEXT_MAX_CHARS - len(empty_git))

    context = warm_context_with_git_text(tmp_path, write_transcript, monkeypatch, git_text)

    assert len(context) == WARM_CONTEXT_MAX_CHARS
    assert git_text in context


def test_a_warm_block_one_char_over_the_limit_gets_its_git_text_shortened(
    tmp_path: Path, write_transcript: TranscriptWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_git = warm_context_with_git_text(tmp_path, write_transcript, monkeypatch, "")
    git_text = "g" * (WARM_CONTEXT_MAX_CHARS - len(empty_git) + 1)

    context = warm_context_with_git_text(tmp_path, write_transcript, monkeypatch, git_text)

    assert len(context) == WARM_CONTEXT_MAX_CHARS
    assert git_text not in context
    assert "g..." in context


def test_a_warm_block_touching_many_files_with_huge_git_text_keeps_the_closing_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "session_handoff.entry.collect_git_info", lambda _cwd: "g" * (WARM_CONTEXT_MAX_CHARS * 2)
    )
    long_paths = [f"{tmp_path}/{'deep/' * 20}file_{index}.py" for index in range(30)]
    request = {
        "type": "assistant",
        "timestamp": JUST_NOW.isoformat(),
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "model": "claude-opus-4-1",
            "content": [
                {"type": "tool_use", "id": f"t{index}", "name": "Read", "input": {"file_path": p}}
                for index, p in enumerate(long_paths)
            ],
            "usage": {"cache_creation": {"ephemeral_1h_input_tokens": 100}},
        },
    }
    transcript = write_raw_transcript(tmp_path, [request])

    output = run(tmp_path, "/handoff", transcript, Recorder())

    context = warm_context_of(output)
    assert len(context) <= WARM_CONTEXT_MAX_CHARS
    assert "Write the file with the Write tool" in context
    assert not [line for line in context.splitlines() if line.startswith("- `")]
