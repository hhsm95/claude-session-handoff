"""The only module that writes to stdout: the JSON answers a `UserPromptSubmit` hook gives."""

import json
import sys
from typing import TextIO

from session_handoff.constants import CONTEXT_BLOCK_TAG, HOOK_EVENT_NAME


def emit_block(reason: str, stream: TextIO | None = None) -> None:
    """Stop the prompt from reaching the model and show `reason` to the user."""
    _write(
        {
            "decision": "block",
            "reason": reason,
            "hookSpecificOutput": {
                "hookEventName": HOOK_EVENT_NAME,
                "suppressOriginalPrompt": True,
            },
        },
        stream,
    )


def context_block(text: str) -> str:
    """`text` inside the handoff tag, exactly as the model receives it."""
    return f"<{CONTEXT_BLOCK_TAG}>\n{text}\n</{CONTEXT_BLOCK_TAG}>"


def emit_context(text: str, stream: TextIO | None = None) -> None:
    """Let the prompt through with `text` added to the model's context, inside the handoff tag."""
    block = context_block(text)
    _write(
        {"hookSpecificOutput": {"hookEventName": HOOK_EVENT_NAME, "additionalContext": block}},
        stream,
    )


def _write(payload: dict[str, object], stream: TextIO | None) -> None:
    target = stream if stream is not None else sys.stdout
    target.write(json.dumps(payload, ensure_ascii=False))
    target.flush()
