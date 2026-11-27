import pytest

from session_handoff.compaction import pre_compact_instructions, strip_analysis
from session_handoff.constants import HANDOFF_MODEL_SECTIONS, PRE_COMPACT_MAX_CHARS


def test_the_instructions_ask_for_the_handoff_title_and_every_model_section() -> None:
    text = pre_compact_instructions()

    assert "# Handoff: <topic>" in text
    for section in HANDOFF_MODEL_SECTIONS:
        assert f"## {section}" in text


def test_the_instructions_add_the_key_files_and_branch_sections_a_compaction_must_write() -> None:
    text = pre_compact_instructions()

    assert "## Key files" in text
    assert "## Branch / commits" in text
    assert "read or edited" in text
    assert "working tree" in text


def test_the_instructions_keep_exact_commands_and_errors_and_the_conversation_language() -> None:
    text = pre_compact_instructions()

    assert "exact commands and error messages" in text
    assert "language the conversation uses" in text


@pytest.mark.parametrize(
    "phrase", ["digest", "added afterwards", "Never invent", "Do not write the Key files"]
)
def test_the_instructions_make_no_claim_that_only_holds_for_the_haiku_pipeline(
    phrase: str,
) -> None:
    assert phrase.lower() not in pre_compact_instructions().lower()


def test_the_instructions_are_cut_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("session_handoff.compaction.PRE_COMPACT_INTRO", "x" * 20_000)

    assert len(pre_compact_instructions()) == PRE_COMPACT_MAX_CHARS


def test_strip_analysis_removes_only_the_leading_block() -> None:
    raw = "<analysis>a</analysis>\nbody </analysis> tail"

    assert strip_analysis(raw) == "body </analysis> tail"


def test_strip_analysis_of_a_summary_with_nothing_else_is_empty() -> None:
    assert strip_analysis("<analysis>only</analysis>\n") == ""
