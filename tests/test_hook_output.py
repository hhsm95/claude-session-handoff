import io
import json

from session_handoff.hook_output import emit_block, emit_context


def test_block_output_hides_the_prompt_and_carries_the_reason() -> None:
    stream = io.StringIO()

    emit_block("Handoff ready", stream)

    assert json.loads(stream.getvalue()) == {
        "decision": "block",
        "reason": "Handoff ready",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "suppressOriginalPrompt": True,
        },
    }


def test_context_output_does_not_block_and_wraps_the_text_in_the_tag() -> None:
    stream = io.StringIO()

    emit_context("write the file", stream)

    output = json.loads(stream.getvalue())
    assert "decision" not in output
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "<session-handoff>\nwrite the file\n</session-handoff>",
        }
    }


def test_output_keeps_non_ascii_text_readable() -> None:
    stream = io.StringIO()

    emit_block("café", stream)

    assert "café" in stream.getvalue()
