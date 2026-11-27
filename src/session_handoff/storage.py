"""Where handoffs and digests live on disk, and how many are kept."""

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from session_handoff.constants import (
    COMPACT_FILE_SUFFIX,
    DIGEST_FILE_SUFFIX,
    FILE_TIMESTAMP_FORMAT,
    HANDOFF_FILE_SUFFIX,
    HANDOFF_STEM_PATTERN,
    HANDOFFS_DIR,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    RETENTION_PAIRS_PER_PROJECT,
    SESSION_ID_FALLBACK,
    SESSION_ID_FILE_CHARS,
)

_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]")
_PAIR_FILE = re.compile(
    rf"(?P<stem>{HANDOFF_STEM_PATTERN})"
    rf"(?:{re.escape(DIGEST_FILE_SUFFIX)}|{re.escape(COMPACT_FILE_SUFFIX)}"
    rf"|{re.escape(HANDOFF_FILE_SUFFIX)})"
)


@dataclass(frozen=True)
class HandoffPaths:
    handoff: Path
    digest: Path


def project_slug(cwd: Path) -> str:
    """Same shape Claude Code uses for the folders under `~/.claude/projects/`."""
    return _NON_ALPHANUMERIC.sub("-", str(cwd.resolve()))


def handoff_paths(
    cwd: Path, session_id: str, now: datetime, base_dir: Path | None = None
) -> HandoffPaths:
    """Paths of the handoff and its digest, named `<UTC timestamp>-<session8>`."""
    base = base_dir if base_dir is not None else Path(HANDOFFS_DIR).expanduser()
    session_part = _NON_ALPHANUMERIC.sub("", session_id)[:SESSION_ID_FILE_CHARS]
    timestamp = now.astimezone(UTC).strftime(FILE_TIMESTAMP_FORMAT)
    stem = f"{timestamp}-{session_part or SESSION_ID_FALLBACK}"
    folder = base / project_slug(cwd)
    return HandoffPaths(
        handoff=folder / f"{stem}{HANDOFF_FILE_SUFFIX}",
        digest=folder / f"{stem}{DIGEST_FILE_SUFFIX}",
    )


def compact_summary_path(
    cwd: Path, session_id: str, now: datetime, base_dir: Path | None = None
) -> Path:
    """Where the summary of a compaction is kept: next to the handoffs, `.compact.md` suffix."""
    handoff = handoff_paths(cwd, session_id, now, base_dir).handoff
    return handoff.with_name(handoff.name.removesuffix(HANDOFF_FILE_SUFFIX) + COMPACT_FILE_SUFFIX)


def write_compact_summary(path: Path, text: str) -> None:
    _write(path, text)


def write_digest(paths: HandoffPaths, text: str) -> None:
    _write(paths.digest, text)


def write_handoff(paths: HandoffPaths, text: str) -> None:
    _write(paths.handoff, text)


def apply_retention(project_dir: Path, keep: int = RETENTION_PAIRS_PER_PROJECT) -> None:
    """Keep the `keep` newest handoff/digest pairs and the `keep` newest compaction summaries.

    The two kinds count apart, so a run of compactions cannot push every handoff out. Only files
    named like a pair or a summary are considered; anything else in the folder is left alone.
    """
    if not project_dir.is_dir():
        return
    groups: dict[bool, dict[str, list[Path]]] = {False: {}, True: {}}
    for path in project_dir.iterdir():
        match = _PAIR_FILE.fullmatch(path.name)
        if match is not None and path.is_file():
            is_summary = path.name.endswith(COMPACT_FILE_SUFFIX)
            groups[is_summary].setdefault(match["stem"], []).append(path)
    for files_by_stem in groups.values():
        for stem in sorted(files_by_stem, reverse=True)[keep:]:
            for path in files_by_stem[stem]:
                path.unlink(missing_ok=True)


def ensure_private_dir(path: Path) -> None:
    """Create `path` if needed and make it owner-only, tightening a folder that already exists."""
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    path.chmod(PRIVATE_DIR_MODE)


def open_private_file(path: Path, flags: int) -> int:
    """Open `path` as an owner-only file: created with that mode, tightened if it already exists."""
    descriptor = os.open(path, flags | os.O_CREAT, PRIVATE_FILE_MODE)
    if hasattr(os, "fchmod"):  # absent on Windows, where POSIX modes do not apply
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    return descriptor


def _write(path: Path, text: str) -> None:
    ensure_private_dir(path.parent.parent)
    ensure_private_dir(path.parent)
    descriptor = open_private_file(path, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
