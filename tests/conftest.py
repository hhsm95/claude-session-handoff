import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from session_handoff.constants import (
    GIT_BRANCH_COMMAND,
    GIT_INSIDE_WORK_TREE_COMMAND,
    GIT_LOG_COMMAND,
    GIT_RECENT_COMMITS,
    GIT_SHORT_HEAD_COMMAND,
    GIT_STATUS_COMMAND,
)

VALID_HANDOFF_TEXT = """# Handoff: parser bug

## Goal
Fix the parser.

## Current state
Half done.

## Next steps
Run the tests.
"""

TranscriptWriter = Callable[..., Path]

# Every git command a collection can run, in the detached-HEAD worst case, as `git_info` spells
# them after the global arguments; `recent_commits` is the number the log asks for.
GIT_WORST_CASE_COMMANDS = (
    GIT_INSIDE_WORK_TREE_COMMAND,
    (*GIT_LOG_COMMAND, f"-{GIT_RECENT_COMMITS}"),
    GIT_STATUS_COMMAND,
    GIT_BRANCH_COMMAND,
    GIT_SHORT_HEAD_COMMAND,
)


@pytest.fixture
def write_transcript(tmp_path: Path) -> TranscriptWriter:
    """Write a two-entry transcript whose last activity is `last_activity`.

    With `cache_1h` the assistant request wrote to the one-hour cache, which makes the session
    eligible for warm mode while it is recent; without it the cache TTL is unknown (cold).
    """

    def write(last_activity: datetime, *, cache_1h: bool) -> Path:
        usage: dict[str, object] = {"input_tokens": 5, "output_tokens": 5}
        if cache_1h:
            usage["cache_creation"] = {"ephemeral_1h_input_tokens": 100}
        records = [
            {
                "type": "user",
                "isSidechain": False,
                "timestamp": last_activity.isoformat(),
                "message": {"role": "user", "content": "Please fix a.py"},
            },
            {
                "type": "assistant",
                "isSidechain": False,
                "timestamp": last_activity.isoformat(),
                "message": {
                    "id": "msg_1",
                    "role": "assistant",
                    "model": "claude-opus-4-1",
                    "content": [{"type": "text", "text": "Looking at a.py"}],
                    "usage": usage,
                },
            },
        ]
        path = tmp_path / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        return path

    return write
