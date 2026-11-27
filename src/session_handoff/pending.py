"""Per-session markers that say "this session was compacted and is not measured yet".

A marker is an empty private file `<base>/pending/<session_id>`; `Stop` checks for it with one
`stat`, so an ordinary turn costs nothing.
"""

import os
import re
from pathlib import Path

from session_handoff.constants import PENDING_DIR_NAME, SESSION_ID_PATTERN
from session_handoff.storage import ensure_private_dir, open_private_file

_SESSION_ID = re.compile(SESSION_ID_PATTERN)


def pending_marker_path(base: Path, session_id: str) -> Path | None:
    """Where the marker of `session_id` lives; `None` when the id cannot be a file name."""
    if _SESSION_ID.fullmatch(session_id) is None:
        return None
    return base / PENDING_DIR_NAME / session_id


def create_pending_marker(base: Path, session_id: str) -> bool:
    """Leave the marker; `False` (and nothing written) when the session id is not valid."""
    marker = pending_marker_path(base, session_id)
    if marker is None:
        return False
    ensure_private_dir(base)
    ensure_private_dir(marker.parent)
    os.close(open_private_file(marker, os.O_WRONLY))
    return True


def has_pending_marker(base: Path, session_id: str) -> bool:
    marker = pending_marker_path(base, session_id)
    return marker is not None and marker.is_file()


def remove_pending_marker(base: Path, session_id: str) -> None:
    marker = pending_marker_path(base, session_id)
    if marker is not None:
        marker.unlink(missing_ok=True)
