"""Git state of the session's working directory, capped and safe to call anywhere."""

import subprocess
from pathlib import Path

from session_handoff.constants import (
    GIT_BRANCH_COMMAND,
    GIT_CLEAN_PLACEHOLDER,
    GIT_DETACHED_PREFIX,
    GIT_GLOBAL_ARGS,
    GIT_INFO_MAX_LINES,
    GIT_INSIDE_WORK_TREE_COMMAND,
    GIT_LOG_COMMAND,
    GIT_RECENT_COMMITS,
    GIT_SHORT_HEAD_COMMAND,
    GIT_STATUS_COMMAND,
    GIT_TIMEOUT_SECONDS,
    GIT_UNAVAILABLE_PLACEHOLDER,
    NO_GIT_MESSAGE,
    NONE_PLACEHOLDER,
)


def collect_git_info(
    cwd: Path,
    *,
    recent_commits: int = GIT_RECENT_COMMITS,
    max_lines: int = GIT_INFO_MAX_LINES,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
) -> str:
    """Branch, recent commits and short status, or `NO_GIT_MESSAGE` outside a repository.

    A git command that fails or times out shows as unavailable, never as an empty result.
    """
    if _run_git(cwd, timeout_seconds, GIT_INSIDE_WORK_TREE_COMMAND) is None:
        return NO_GIT_MESSAGE
    commits = _run_git(cwd, timeout_seconds, (*GIT_LOG_COMMAND, f"-{recent_commits}"))
    status = _run_git(cwd, timeout_seconds, GIT_STATUS_COMMAND)
    lines = [
        f"Branch: {_current_branch(cwd, timeout_seconds)}",
        "Recent commits:",
        *_lines_of(commits, empty=NONE_PLACEHOLDER),
        "Status:",
        *_lines_of(status, empty=GIT_CLEAN_PLACEHOLDER),
    ]
    return "\n".join(lines[:max_lines])


def _current_branch(cwd: Path, timeout_seconds: float) -> str:
    branch = _run_git(cwd, timeout_seconds, GIT_BRANCH_COMMAND)
    if branch is None:
        return GIT_UNAVAILABLE_PLACEHOLDER
    if branch.strip():
        return branch.strip()
    short_hash = _run_git(cwd, timeout_seconds, GIT_SHORT_HEAD_COMMAND)
    if short_hash is None:
        return GIT_UNAVAILABLE_PLACEHOLDER
    return f"{GIT_DETACHED_PREFIX}{short_hash.strip()})" if short_hash.strip() else NONE_PLACEHOLDER


def _lines_of(output: str | None, *, empty: str) -> list[str]:
    if output is None:
        return [GIT_UNAVAILABLE_PLACEHOLDER]
    return output.splitlines() or [empty]


def _run_git(cwd: Path, timeout_seconds: float, command: tuple[str, ...]) -> str | None:
    """Stdout of a git command, or None if it fails, times out or git is missing."""
    try:
        result = subprocess.run(
            ["git", *GIT_GLOBAL_ARGS, *command],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None
