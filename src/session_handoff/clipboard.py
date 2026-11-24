"""Copy text to the macOS clipboard; every failure is just `False`."""

import shutil
import subprocess
from collections.abc import Callable

from session_handoff.constants import CLIPBOARD_COMMAND, CLIPBOARD_TIMEOUT_SECONDS


def copy_to_clipboard(
    text: str, *, find_command: Callable[[str], str | None] = shutil.which
) -> bool:
    """Whether `pbcopy` took the text. A missing command or any failure never raises."""
    executable = find_command(CLIPBOARD_COMMAND)
    if executable is None:
        return False
    try:
        completed = subprocess.run(
            [executable],
            input=text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=CLIPBOARD_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0
