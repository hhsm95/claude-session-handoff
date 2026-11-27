"""The only module that writes to stdout: the answers the plugin's hooks give."""

import json
import sys
from typing import TextIO

from session_handoff.constants import CONTEXT_BLOCK_TAG, USER_PROMPT_SUBMIT_EVENT_NAME


def emit_block(reason: str, stream: TextIO | None = None) -> None:
    """Stop the prompt from reaching the model and show `reason` to the user."""
    _write(
        json.dumps(
            {
                "decision": "block",
                "reason": reason,
                "hookSpecificOutput": {
                    "hookEventName": USER_PROMPT_SUBMIT_EVENT_NAME,
                    "suppressOriginalPrompt": True,
                },
            },
            ensure_ascii=False,
        ),
        stream,
    )


def context_block(text: str) -> str:
    """`text` inside the handoff tag, exactly as the model receives it."""
    return f"<{CONTEXT_BLOCK_TAG}>\n{text}\n</{CONTEXT_BLOCK_TAG}>"


def emit_context(text: str, stream: TextIO | None = None) -> None:
    """Let the prompt through with `text` added to the model's context, inside the handoff tag."""
    block = context_block(text)
    _write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": USER_PROMPT_SUBMIT_EVENT_NAME,
                    "additionalContext": block,
                }
            },
            ensure_ascii=False,
        ),
        stream,
    )


def emit_text(text: str, stream: TextIO | None = None) -> None:
    """Plain stdout, the form `PreCompact` reads as extra compaction instructions."""
    _write(text, stream)


def _write(text: str, stream: TextIO | None) -> None:
    target = stream if stream is not None else sys.stdout
    target.write(text)
    target.flush()
