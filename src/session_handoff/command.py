"""Recognise the `/handoff` command in a raw prompt and split its flags from the focus."""

import re
from dataclasses import dataclass

from session_handoff.constants import HAIKU_FLAG, HANDOFF_COMMAND_PATTERN, SAVINGS_COMMAND_PATTERN

_COMMAND = re.compile(HANDOFF_COMMAND_PATTERN)
_SAVINGS_COMMAND = re.compile(SAVINGS_COMMAND_PATTERN)


@dataclass(frozen=True)
class HandoffCommand:
    force_haiku: bool
    focus: str


def is_savings_command(prompt: str) -> bool:
    """Whether the prompt is `/savings`, which takes no arguments and ignores any given."""
    return _SAVINGS_COMMAND.match(prompt) is not None


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
