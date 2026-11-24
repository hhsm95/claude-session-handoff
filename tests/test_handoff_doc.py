from pathlib import Path

import pytest

from session_handoff.constants import NO_GIT_MESSAGE
from session_handoff.digest import FileActivity
from session_handoff.handoff_doc import (
    HandoffExtras,
    InvalidHandoffError,
    assemble_handoff,
    build_system_prompt,
    format_instructions,
    render_deterministic_sections,
    render_key_files,
    validate_model_answer,
)

DIGEST_PATH = Path("/handoffs/project/20260501-123005-0f3a9c21.digest.md")
DIGEST_LINE = f"Full detail (read only if needed): {DIGEST_PATH}"
GIT_TEXT = "Branch: main\nRecent commits:\n(none)\nStatus:\n(clean)"

VALID_ANSWER = """# Handoff: parser bug

## Goal
Fix the parser.

## Current state
Half done.

## Decisions and why
Used a table.

## Ruled out and why
Regex.

## Next steps
Run the tests.

## How to verify
pytest
"""


def activity(path: str, reads: int = 0, edits: int = 0, writes: int = 0) -> FileActivity:
    return FileActivity(path, reads, edits, writes)


# --- system prompt ------------------------------------------------------------------------------


def test_prompt_lists_the_fixed_headings_in_order() -> None:
    prompt = build_system_prompt("")

    headings = [
        "# Handoff: <topic>",
        "## Goal",
        "## Current state",
        "## Decisions and why",
        "## Ruled out and why",
        "## Next steps",
        "## How to verify",
    ]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_prompt_states_the_size_limit_it_is_given() -> None:
    assert "at most 1234 characters" in build_system_prompt("", max_chars=1234)


def test_prompt_asks_for_the_conversation_language_and_no_invented_files() -> None:
    prompt = build_system_prompt("")

    assert "language the conversation uses" in prompt
    assert "Never invent files" in prompt


def test_prompt_says_the_end_of_the_conversation_is_the_current_state() -> None:
    prompt = build_system_prompt("")

    assert "The end of the conversation is the current state" in prompt
    assert "later turns override earlier ones" in prompt


def test_prompt_makes_the_repository_state_section_authoritative() -> None:
    prompt = build_system_prompt("")

    assert "The Repository state section is authoritative" in prompt
    assert "never contradict it" in prompt


def test_format_instructions_do_not_carry_the_cold_only_rules() -> None:
    assert "Repository state" not in format_instructions("")


def test_prompt_has_the_priority_line_and_ordering_instruction_with_a_focus() -> None:
    prompt = build_system_prompt("continue with the parser bug")

    assert "User priority: continue with the parser bug" in prompt
    assert "Order Goal and Next steps" in prompt


def test_prompt_has_no_priority_without_a_focus() -> None:
    prompt = build_system_prompt("")

    assert "User priority" not in prompt
    assert "Order Goal and Next steps" not in prompt


def test_format_instructions_carry_the_same_format_limit_and_focus() -> None:
    instructions = format_instructions("fix the cache", max_chars=777)

    assert "## Next steps" in instructions
    assert "at most 777 characters" in instructions
    assert "User priority: fix the cache" in instructions
    assert "You write handoff documents" not in instructions


# --- answer validation --------------------------------------------------------------------------


def test_valid_answer_is_returned_stripped() -> None:
    assert validate_model_answer(f"\n  {VALID_ANSWER}\n\n") == VALID_ANSWER.strip()


@pytest.mark.parametrize("fence", ["```markdown", "```md", "```"])
def test_a_single_outer_code_fence_is_stripped(fence: str) -> None:
    fenced = f"{fence}\n{VALID_ANSWER.strip()}\n```\n"

    assert validate_model_answer(fenced) == VALID_ANSWER.strip()


def test_only_the_outer_fence_is_stripped_and_inner_fences_stay() -> None:
    with_code = VALID_ANSWER.strip() + "\n\n```\nuv run pytest\n```"

    assert validate_model_answer(f"```markdown\n{with_code}\n```") == with_code
    assert validate_model_answer(with_code) == with_code


@pytest.mark.parametrize("answer", ["", "   \n", "I cannot help with that."])
def test_empty_or_refusing_answer_is_rejected(answer: str) -> None:
    with pytest.raises(InvalidHandoffError):
        validate_model_answer(answer)


def test_answer_with_a_preamble_before_the_title_is_rejected() -> None:
    with pytest.raises(InvalidHandoffError, match="# Handoff"):
        validate_model_answer("Sure! Here it is:\n" + VALID_ANSWER)


@pytest.mark.parametrize("missing", ["Goal", "Current state", "Next steps"])
def test_answer_missing_a_required_section_is_rejected(missing: str) -> None:
    broken = VALID_ANSWER.replace(f"## {missing}\n", "")

    with pytest.raises(InvalidHandoffError, match=missing):
        validate_model_answer(broken)


def test_optional_sections_may_be_absent() -> None:
    short = "# Handoff: x\n## Goal\ng\n## Current state\nc\n## Next steps\nn"

    assert validate_model_answer(short) == short


def test_section_name_only_in_prose_does_not_count_as_a_heading() -> None:
    prose = "# Handoff: x\n## Goal\nthe Current state and Next steps are fine\n"

    with pytest.raises(InvalidHandoffError):
        validate_model_answer(prose)


# --- deterministic sections ---------------------------------------------------------------------


def test_key_files_are_ordered_by_total_activity_and_limited() -> None:
    files = [
        activity("quiet.py", reads=1),
        activity("busy.py", reads=2, edits=3, writes=1),
        activity("middle.py", edits=2),
    ]

    section = render_key_files(files, limit=2)

    lines = section.splitlines()
    assert lines[0] == "## Key files"
    assert [line.split("`")[1] for line in lines[1:]] == ["busy.py", "middle.py"]


def test_bash_refs_count_toward_key_file_ranking() -> None:
    files = [activity("read_once.py", reads=1), FileActivity("via_bash.py", 0, 0, 0, bash_refs=4)]

    section = render_key_files(files, limit=1)

    assert section.splitlines()[1].startswith("- `via_bash.py`")


def test_an_edited_file_outranks_a_file_with_more_bash_refs() -> None:
    files = [FileActivity("shelled.sh", 0, 0, 0, bash_refs=2), activity("edited.py", edits=1)]

    section = render_key_files(files, limit=2)

    assert [line.split("`")[1] for line in section.splitlines()[1:]] == ["edited.py", "shelled.sh"]


def test_a_read_outranks_a_single_bash_ref() -> None:
    files = [FileActivity("shelled.sh", 0, 0, 0, bash_refs=1), activity("read.py", reads=1)]

    section = render_key_files(files, limit=2)

    assert [line.split("`")[1] for line in section.splitlines()[1:]] == ["read.py", "shelled.sh"]


def test_key_files_show_the_raw_counts_even_when_ranked_by_weight() -> None:
    files = [FileActivity("mixed.py", 1, 2, 0, bash_refs=3)]

    section = render_key_files(files, limit=1)

    assert "1 reads, 2 edits, 0 writes, 3 bash refs" in section


def test_key_files_with_equal_activity_keep_their_original_order() -> None:
    files = [activity("b.py", reads=1), activity("a.py", reads=1)]

    section = render_key_files(files, limit=10)

    assert [line.split("`")[1] for line in section.splitlines()[1:]] == ["b.py", "a.py"]


def test_key_files_without_activity_say_none() -> None:
    assert render_key_files([], limit=5) == "## Key files\n(none)"


def test_deterministic_sections_hold_files_git_and_the_digest_line_in_order() -> None:
    text = render_deterministic_sections(
        HandoffExtras([activity("a.py", edits=1)], GIT_TEXT, DIGEST_PATH)
    )

    assert text.index("## Key files") < text.index("## Branch / commits") < text.index(DIGEST_LINE)
    assert text.endswith(DIGEST_LINE)
    assert GIT_TEXT in text


def test_project_without_git_says_so_in_the_branch_section() -> None:
    text = render_deterministic_sections(HandoffExtras([], NO_GIT_MESSAGE, DIGEST_PATH))

    assert f"## Branch / commits\n{NO_GIT_MESSAGE}" in text


# --- assembly -----------------------------------------------------------------------------------


def test_assembled_handoff_is_model_text_then_deterministic_sections() -> None:
    files = [activity("a.py", edits=1)]

    document = assemble_handoff(VALID_ANSWER, HandoffExtras(files, GIT_TEXT, DIGEST_PATH))

    assert document == (
        VALID_ANSWER.strip()
        + "\n\n"
        + render_deterministic_sections(HandoffExtras(files, GIT_TEXT, DIGEST_PATH))
    )


def test_document_within_the_cap_is_not_trimmed() -> None:
    files = [activity(f"f{index}.py", reads=1) for index in range(5)]

    document = assemble_handoff(
        VALID_ANSWER, HandoffExtras(files, GIT_TEXT, DIGEST_PATH), max_chars=10_000
    )

    assert all(f"f{index}.py" in document for index in range(5))
    assert VALID_ANSWER.strip() in document


def test_key_files_are_trimmed_before_the_model_text() -> None:
    files = [activity(f"file{index}.py", reads=100 - index) for index in range(20)]
    full = assemble_handoff(
        VALID_ANSWER, HandoffExtras(files, GIT_TEXT, DIGEST_PATH), max_chars=10_000
    )

    document = assemble_handoff(
        VALID_ANSWER, HandoffExtras(files, GIT_TEXT, DIGEST_PATH), max_chars=len(full) - 1
    )

    assert len(document) <= len(full) - 1
    assert VALID_ANSWER.strip() in document
    assert "file0.py" in document
    assert "file19.py" not in document


def floor_length(files: list[FileActivity]) -> int:
    """Length of the smallest document: a one-character model text and no key files."""
    return len(
        assemble_handoff(
            "#", HandoffExtras(files, GIT_TEXT, DIGEST_PATH), max_chars=10_000, key_files_limit=0
        )
    )


def test_model_text_is_cut_at_the_end_once_key_files_are_gone() -> None:
    files = [activity("file.py", reads=1)]
    cap = floor_length(files) + 30

    document = assemble_handoff(
        VALID_ANSWER, HandoffExtras(files, GIT_TEXT, DIGEST_PATH), max_chars=cap
    )

    assert len(document) <= cap
    assert document.startswith("# Handoff: parser bug")
    assert VALID_ANSWER.strip() not in document
    assert "file.py" not in document
    assert "omitted" in document


def test_digest_line_survives_every_trimming_level() -> None:
    files = [activity(f"file{index}.py", reads=1) for index in range(30)]
    floor = floor_length(files)

    for spare in (0, 5, 500, 5000):
        document = assemble_handoff(
            VALID_ANSWER, HandoffExtras(files, GIT_TEXT, DIGEST_PATH), max_chars=floor + spare
        )
        assert document.endswith(DIGEST_LINE)
        assert len(document) <= floor + spare
