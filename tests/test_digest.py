import sys
from pathlib import Path

import pytest

from session_handoff.bash_refs import files_in_command
from session_handoff.constants import CONVERSATION_HEADING, MENTION_GUARD
from session_handoff.digest import (
    EmptyConversationError,
    FileActivity,
    build_digest,
    collect_file_activity,
)
from session_handoff.transcript import (
    Entry,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    read_transcript,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "session_2_1_330.jsonl"


def tool_call(name: str, **params: object) -> Entry:
    return Entry(role="assistant", blocks=(ToolUseBlock("toolu_x", name, params),))


def say(role: str, text: str) -> Entry:
    return Entry(role="user" if role == "user" else "assistant", blocks=(TextBlock(text),))


def conversation_of(digest: str) -> str:
    return digest.split(CONVERSATION_HEADING + "\n\n", 1)[1]


def fixture_digest() -> str:
    return build_digest(read_transcript(FIXTURE).entries)


def test_read_call_shows_its_path_and_an_ok_status_without_the_content() -> None:
    digest = fixture_digest()

    assert "- Read(file_path=src/a.py)" in digest
    assert "- Read: ok" in digest
    assert "FILE-BODY-LINE" not in digest


def test_error_result_keeps_only_its_first_lines() -> None:
    digest = fixture_digest()

    assert "- Bash: error" in digest
    assert "ERR-15" in digest
    assert "ERR-16" not in digest


def test_error_lines_limit_is_configurable() -> None:
    entries = (
        Entry("user", (ToolResultBlock("toolu_1", True, "e1\ne2\ne3\ne4"),)),
        say("assistant", "after"),
    )

    digest = build_digest(entries, error_lines=2)

    assert "e2" in digest
    assert "e3" not in digest


def test_result_is_labelled_with_the_name_of_the_call_it_answers() -> None:
    entries = (
        Entry("assistant", (ToolUseBlock("toolu_7", "Grep", {"pattern": "needle"}),)),
        Entry("user", (ToolResultBlock("toolu_7", False, "match"),)),
    )

    digest = build_digest(entries)

    assert "- Grep(pattern=needle)" in digest
    assert "- Grep: ok" in digest


def test_result_of_an_unknown_call_is_still_reported() -> None:
    digest = build_digest((Entry("user", (ToolResultBlock("toolu_gone", False, "x"),)),))

    assert "- tool: ok" in digest


def test_tool_call_keeps_only_the_key_parameters() -> None:
    digest = build_digest(
        (tool_call("Edit", file_path="src/a.py", old_string="SECRET-BODY", new_string="y"),)
    )

    assert "Edit(file_path=src/a.py)" in digest
    assert "SECRET-BODY" not in digest


def test_multiline_command_is_collapsed_and_a_huge_one_is_shortened() -> None:
    digest = build_digest(
        (tool_call("Bash", command="ls\n  -la"), tool_call("Bash", command="x" * 5000))
    )

    assert "Bash(command=ls -la)" in digest
    assert "x" * 1000 not in digest


def test_subagent_and_thinking_content_does_not_reach_the_digest() -> None:
    digest = fixture_digest()

    for leaked in ("SIDECHAIN-TEXT", "sidechain_only.py", "PRIVATE-THINKING", "API-ERROR-TEXT"):
        assert leaked not in digest


def test_compaction_summary_opens_the_conversation() -> None:
    conversation = conversation_of(fixture_digest())

    assert conversation.startswith("### Compaction summary\nSECOND-SUMMARY text")
    assert "BEFORE-FIRST-BOUNDARY" not in conversation


def test_secrets_in_commands_and_errors_are_redacted() -> None:
    digest = fixture_digest()

    assert "export API_TOKEN=[REDACTED]" in digest
    assert "abc123" not in digest


def test_mentions_in_prompts_and_tool_calls_are_neutralized_in_the_digest() -> None:
    entries = (
        say("user", "please read @/abs/secret.txt"),
        tool_call("Bash", command="cat @notes.md"),
    )

    digest = build_digest(entries)

    assert f"@{MENTION_GUARD}/abs/secret.txt" in digest
    assert f"cat @{MENTION_GUARD}notes.md" in digest
    assert "@/abs" not in digest
    assert "@notes" not in digest


def test_mentions_in_the_file_list_are_neutralized() -> None:
    digest = build_digest((say("user", "go"), tool_call("Read", file_path="node_modules/@types/x")))

    assert f"@{MENTION_GUARD}types" in digest
    assert "@types" not in digest


def test_a_conversation_with_nothing_to_render_is_an_error() -> None:
    thinking_only = Entry(role="assistant", blocks=())

    with pytest.raises(EmptyConversationError):
        build_digest((thinking_only,))
    with pytest.raises(EmptyConversationError):
        build_digest(())


def test_meta_entries_and_local_command_bookkeeping_are_left_out() -> None:
    entries = (
        say("user", "real first prompt"),
        Entry(role="user", blocks=(TextBlock("META-INJECTED"),), is_meta=True),
        say("user", "<local-command-caveat>caveat text</local-command-caveat>"),
        say("user", "<command-name>/context</command-name>"),
        say("user", "<local-command-stdout>context grid</local-command-stdout>"),
        say("assistant", "an answer"),
    )

    digest = build_digest(entries)

    for left_out in ("META-INJECTED", "caveat text", "/context", "context grid"):
        assert left_out not in digest
    assert "real first prompt" in digest
    assert "an answer" in digest


def test_bash_mode_input_and_output_are_kept_as_real_user_work() -> None:
    entries = (
        say("user", "<bash-input>pytest -q</bash-input>"),
        say("user", "<bash-stdout>3 passed</bash-stdout>"),
    )

    digest = build_digest(entries)

    assert "<bash-input>pytest -q</bash-input>" in digest
    assert "3 passed" in digest


def test_left_out_entries_do_not_take_a_slot_among_the_opening_user_messages() -> None:
    meta = Entry(role="user", blocks=(TextBlock("META-INJECTED"),), is_meta=True)
    entries = (
        meta,
        say("user", "FIRST-GOAL " + "g" * 40),
        *numbered_conversation(30)[1:],
    )

    conversation = conversation_of(build_digest(entries, max_chars=1200))

    assert conversation.startswith("### User\nFIRST-GOAL")
    assert "META-INJECTED" not in conversation


def test_secret_inside_an_error_result_is_redacted() -> None:
    entries = (Entry("user", (ToolResultBlock("t", True, "denied for ghp_abcdef123456"),)),)

    assert "ghp_abcdef123456" not in build_digest(entries)


def test_file_counts_are_computed_per_file_from_tool_parameters() -> None:
    entries = (
        tool_call("Read", file_path="src/a.py"),
        tool_call("Read", file_path="src/a.py"),
        tool_call("Edit", file_path="src/a.py"),
        tool_call("Edit", file_path="src/a.py"),
        tool_call("Edit", file_path="src/a.py"),
    )

    assert collect_file_activity(entries) == (FileActivity("src/a.py", reads=2, edits=3, writes=0),)


def test_write_and_notebook_edit_are_counted_and_files_keep_first_touch_order() -> None:
    entries = (
        tool_call("Write", file_path="b.txt"),
        tool_call("NotebookEdit", notebook_path="nb.ipynb"),
        tool_call("Read", file_path="a.py"),
    )

    assert collect_file_activity(entries) == (
        FileActivity("b.txt", reads=0, edits=0, writes=1),
        FileActivity("nb.ipynb", reads=0, edits=1, writes=0),
        FileActivity("a.py", reads=1, edits=0, writes=0),
    )


def test_other_tools_and_missing_or_mistyped_paths_do_not_count() -> None:
    entries = (
        tool_call("Bash", file_path="nope.py"),
        tool_call("Read"),
        tool_call("Read", file_path=7),
    )

    assert collect_file_activity(entries) == ()


def test_bash_arguments_naming_project_files_count_as_bash_refs(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    entries = (
        tool_call("Read", file_path="a.py"),
        tool_call("Bash", command="sed -n '1,9p' a.py"),
        tool_call("Bash", command="wc -l a.py missing.py"),
    )

    assert collect_file_activity(entries, cwd=tmp_path) == (
        FileActivity("a.py", reads=1, edits=0, writes=0, bash_refs=2),
    )


def test_without_a_project_directory_bash_commands_are_not_inspected(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")

    assert collect_file_activity((tool_call("Bash", command="cat a.py"),)) == ()


def test_bash_refs_are_shown_only_when_there_are_some(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    entries = (tool_call("Bash", command="cat a.py"), tool_call("Read", file_path="b.py"))

    digest = build_digest(entries, cwd=tmp_path)

    assert "`a.py`: 0 reads, 0 edits, 0 writes, 1 bash refs" in digest
    assert "`b.py`: 1 reads, 0 edits, 0 writes\n" in f"{digest}\n"


def test_file_list_appears_in_the_digest_with_its_counts() -> None:
    digest = fixture_digest()

    assert "`src/a.py`: 1 reads, 1 edits, 0 writes" in digest
    assert "sidechain_only" not in digest


def test_digest_without_file_activity_says_so() -> None:
    assert "(none)" in build_digest((say("user", "hello"),))


def numbered_conversation(pairs: int) -> tuple[Entry, ...]:
    entries = [say("user", "FIRST-GOAL " + "g" * 40)]
    for index in range(pairs):
        entries.append(say("assistant", f"reply-{index:02d} " + "r" * 40))
        entries.append(say("user", f"prompt-{index:02d} " + "p" * 40))
    entries.append(say("assistant", "LAST-TURN"))
    return tuple(entries)


def test_digest_within_the_cap_is_not_cut() -> None:
    digest = build_digest(numbered_conversation(2), max_chars=10_000)

    assert "turns omitted" not in digest
    assert "reply-00" in digest


def test_oversized_digest_keeps_first_user_message_and_last_turn_and_marks_the_gap() -> None:
    cap = 900

    digest = build_digest(numbered_conversation(30), max_chars=cap)
    conversation = conversation_of(digest)

    assert len(digest) <= cap
    assert conversation.startswith("### User\nFIRST-GOAL")
    assert conversation.endswith("LAST-TURN")
    assert "turns omitted" in conversation
    assert "reply-10" not in conversation


def test_omitted_turn_count_matches_the_turns_actually_dropped() -> None:
    entries = numbered_conversation(30)
    cap = 900

    conversation = conversation_of(build_digest(entries, max_chars=cap))

    kept = conversation.count("### ")
    marker = next(part for part in conversation.split("[... ")[1:])
    omitted = int(marker.split(" ")[0])
    assert kept + omitted == len(entries)


def test_oversized_digest_keeps_the_first_few_user_messages_and_the_recent_stretch() -> None:
    conversation = conversation_of(build_digest(numbered_conversation(30), max_chars=1200))

    assert "prompt-00" in conversation
    assert "reply-29" in conversation
    assert "reply-15" not in conversation


def test_a_last_turn_larger_than_the_cap_is_cut_but_the_digest_stays_within_it() -> None:
    entries = (say("user", "goal"), say("assistant", "z" * 5000 + "THE-END"))

    digest = build_digest(entries, max_chars=700)

    assert len(digest) <= 700
    assert digest.endswith("THE-END")


def test_an_absolute_file_tool_path_and_a_relative_bash_ref_share_one_row(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    entries = (
        tool_call("Read", file_path=str(tmp_path / "a.py")),
        tool_call("Bash", command="sed -n 1,5p a.py"),
    )

    assert collect_file_activity(entries, cwd=tmp_path) == (
        FileActivity("a.py", reads=1, edits=0, writes=0, bash_refs=1),
    )


def test_a_file_tool_path_outside_the_project_stays_absolute(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = str(tmp_path / "elsewhere" / "notes.md")

    activity = collect_file_activity((tool_call("Write", file_path=outside),), cwd=project)

    assert activity == (FileActivity(outside, reads=0, edits=0, writes=1),)


def test_file_tool_paths_are_kept_as_given_without_a_project_directory(tmp_path: Path) -> None:
    absolute = str(tmp_path / "a.py")

    activity = collect_file_activity((tool_call("Read", file_path=absolute),))

    assert activity == (FileActivity(absolute, reads=1, edits=0, writes=0),)


def test_an_assistant_text_that_starts_like_a_local_command_is_kept() -> None:
    entries = (
        say("user", "a real question"),
        say("assistant", "<command-name>/context</command-name> shows the grid"),
    )

    assert "shows the grid" in build_digest(entries)


def test_a_local_command_record_with_leading_whitespace_is_still_left_out() -> None:
    entries = (
        say("user", "a real question"),
        say("user", "\n  <local-command-stdout>context grid</local-command-stdout>"),
    )

    assert "context grid" not in build_digest(entries)


def test_a_secret_in_a_file_list_path_is_redacted() -> None:
    entries = (say("user", "go"), tool_call("Read", file_path="notes/API_KEY=abc123.txt"))

    digest = build_digest(entries)

    assert "abc123" not in digest
    assert "API_KEY=[REDACTED]" in digest


def symlinked_project(tmp_path: Path) -> tuple[Path, Path]:
    """A project holding `a.py`, and a symlink `link` pointing at it."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("a", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(project, target_is_directory=True)
    return project, link


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privileges on Windows")
def test_an_absolute_path_under_the_real_project_and_a_bash_ref_through_a_symlink_share_one_row(
    tmp_path: Path,
) -> None:
    project, link = symlinked_project(tmp_path)
    entries = (
        tool_call("Read", file_path=str(project / "a.py")),
        tool_call("Bash", command="cat a.py"),
    )

    assert collect_file_activity(entries, cwd=link) == (
        FileActivity("a.py", reads=1, edits=0, writes=0, bash_refs=1),
    )


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privileges on Windows")
def test_bash_refs_through_a_symlinked_project_directory_are_relative_to_the_link(
    tmp_path: Path,
) -> None:
    _, link = symlinked_project(tmp_path)

    assert files_in_command("cat a.py", link) == ("a.py",)
