"""Files a `Bash` command refers to, for sessions that read and edit through the shell."""

import re
import shlex
from collections.abc import Iterator
from pathlib import Path

from session_handoff.constants import (
    BASH_CONTROL_CHARS,
    BASH_ENV_ASSIGNMENT_PATTERN,
    BASH_FLAG_PREFIX,
    BASH_PUNCTUATION_CHARS,
    BASH_WHITESPACE,
)
from session_handoff.project_paths import project_relative, resolve_root

_ENV_ASSIGNMENT = re.compile(BASH_ENV_ASSIGNMENT_PATTERN)


def files_in_command(command: str, cwd: Path) -> tuple[str, ...]:
    """Arguments of `command` that name existing regular files inside `cwd`, once each, in order.

    Paths are reported relative to `cwd`. An unparsable command (e.g. an unclosed quote) names
    nothing; this is an inference from the command text, never a confirmed read.
    """
    tokens = _tokens(command)
    if tokens is None:
        return ()
    root = resolve_root(cwd)
    found: dict[str, None] = {}
    for token in _arguments(tokens):
        relative = _project_file(token, root)
        if relative is not None:
            found.setdefault(relative, None)
    return tuple(found)


def _arguments(tokens: list[str]) -> Iterator[str]:
    """The tokens that are not the program of their command segment.

    The program is the first token of the command and the first after a control operator,
    skipping `NAME=value` prefixes: `tools/run.sh x` runs the script, it does not read it.
    """
    expects_program = True
    for token in tokens:
        if all(char in BASH_CONTROL_CHARS for char in token):
            expects_program = True
        elif expects_program and _ENV_ASSIGNMENT.match(token):
            continue
        elif expects_program:
            expects_program = False
        else:
            yield token


def _tokens(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=BASH_PUNCTUATION_CHARS)
    lexer.whitespace = BASH_WHITESPACE  # newline is not blank: it ends a command
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _project_file(token: str, root: Path) -> str | None:
    # Digit-only tokens are file descriptors (`2>&1`) far more often than files named `1`.
    if not token or token.isdigit() or token.startswith(BASH_FLAG_PREFIX):
        return None
    relative = project_relative(token, root)
    if relative is None:
        return None
    try:
        return relative if (root / relative).is_file() else None
    except (OSError, ValueError):
        return None
