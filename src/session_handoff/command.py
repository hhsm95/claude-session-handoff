"""Recognise the `/handoff` command in a raw prompt and split its flags from the focus."""

import re
from dataclasses import dataclass

from session_handoff.constants import COMMAND_PATTERN, HAIKU_FLAG

_COMMAND = re.compile(COMMAND_PATTERN)


@dataclass(frozen=True)
class HandoffCommand:
    force_haiku: bool
    focus: str


def parse_command(prompt: str) -> HandoffCommand | None:
    """The parsed command, or `None` when the prompt is not `/handoff`."""
    match = _COMMAND.match(prompt)
    if match is None:
        return None
    words = prompt[match.end() :].split()
    focus_words = [word for word in words if word != HAIKU_FLAG]
    return HandoffCommand(
        force_haiku=len(focus_words) != len(words),
        focus=" ".join(focus_words),
    )
