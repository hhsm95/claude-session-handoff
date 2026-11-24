"""One spelling for the files of a session, whichever tool named them."""

from pathlib import Path


def project_relative(path: str, root: Path) -> str | None:
    """`path` as a POSIX path relative to `root`, or None if it resolves outside `root`.

    `root` must already be resolved. Relative paths are taken from `root`; symlinks are followed,
    so a link that leaves the project counts as outside. Nothing here raises: a path that cannot
    be resolved (NUL byte, name too long, symlink loop) is None. `Path.resolve()` raises
    `RuntimeError` on a loop in Python 3.11 and 3.12 (not in 3.13+), and one bad path must never
    abort the whole handoff.
    """
    candidate = Path(path)
    try:
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not resolved.is_relative_to(root):
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return resolved.relative_to(root).as_posix()


def normalize_path(path: str, cwd: Path | None) -> str:
    """`path` relative to `cwd` when `cwd` is known and holds it, else `path` as given."""
    if cwd is None:
        return path
    return project_relative(path, resolve_root(cwd)) or path


def resolve_root(cwd: Path) -> Path:
    """`cwd` resolved; as given if it cannot be (the paths under it then count as outside)."""
    try:
        return cwd.resolve()
    except (OSError, RuntimeError):
        return cwd
