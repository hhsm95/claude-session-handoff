import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from session_handoff.storage import (
    HandoffPaths,
    apply_retention,
    handoff_paths,
    project_slug,
    write_digest,
    write_handoff,
)

SESSION_ID = "0f3a9c21-7b1d-4e55-9a10-2d6c8e4b7f90"


def at(second: int) -> datetime:
    return datetime(2026, 5, 1, 12, 30, second, tzinfo=UTC)


def make_paths(tmp_path: Path, second: int = 5, cwd: Path | None = None) -> HandoffPaths:
    return handoff_paths(cwd or tmp_path / "project", SESSION_ID, at(second), tmp_path / "base")


def save_pair(paths: HandoffPaths) -> None:
    write_digest(paths, "digest text")
    write_handoff(paths, "handoff text")


def test_slug_replaces_every_non_alphanumeric_character_with_a_dash(tmp_path: Path) -> None:
    project = tmp_path / "my project_v2.0"
    project.mkdir()

    slug = project_slug(project)

    assert slug.endswith("-my-project-v2-0")
    assert re.fullmatch(r"[A-Za-z0-9-]+", slug)


def test_slug_follows_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    assert project_slug(link) == project_slug(real)


def test_paths_live_under_the_base_dir_in_a_project_folder(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    paths = make_paths(tmp_path, cwd=project)

    assert paths.handoff.parent == tmp_path / "base" / project_slug(project)
    assert paths.digest.parent == paths.handoff.parent


def test_file_names_hold_the_utc_timestamp_and_the_first_eight_session_chars(
    tmp_path: Path,
) -> None:
    paths = make_paths(tmp_path)

    assert paths.handoff.name == "20260501-123005-0f3a9c21.md"
    assert paths.digest.name == "20260501-123005-0f3a9c21.digest.md"


def test_timestamp_is_taken_in_utc_whatever_the_clock_zone(tmp_path: Path) -> None:
    local = datetime(2026, 5, 1, 7, 30, 5, tzinfo=timezone(timedelta(hours=-5)))

    paths = handoff_paths(tmp_path, SESSION_ID, local, tmp_path / "base")

    assert paths.handoff.name.startswith("20260501-123005-")


def test_unsafe_session_id_characters_cannot_leave_the_folder(tmp_path: Path) -> None:
    paths = handoff_paths(tmp_path, "../../etc/passwd", at(1), tmp_path / "base")

    assert paths.handoff.parent == tmp_path / "base" / project_slug(tmp_path)
    assert os.sep not in paths.handoff.name


def test_home_is_the_default_base_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    paths = handoff_paths(tmp_path / "project", SESSION_ID, at(1))

    assert paths.handoff.is_relative_to(tmp_path / ".claude" / "handoffs")


def test_writing_creates_the_folder_and_both_files(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)

    save_pair(paths)

    assert paths.digest.read_text(encoding="utf-8") == "digest text"
    assert paths.handoff.read_text(encoding="utf-8") == "handoff text"


def test_digest_can_be_saved_without_a_handoff(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)

    write_digest(paths, "digest text")

    assert paths.digest.exists()
    assert not paths.handoff.exists()


def test_text_is_written_as_utf8(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)

    write_handoff(paths, "decisión: añadir ñ")

    assert paths.handoff.read_bytes() == "decisión: añadir ñ".encode()


def test_retention_deletes_the_oldest_pairs_and_keeps_the_newest(tmp_path: Path) -> None:
    pairs = [make_paths(tmp_path, second) for second in range(1, 6)]
    for pair in pairs:
        save_pair(pair)

    apply_retention(pairs[0].handoff.parent, keep=3)

    for old in pairs[:2]:
        assert not old.handoff.exists()
        assert not old.digest.exists()
    for recent in pairs[2:]:
        assert recent.handoff.exists()
        assert recent.digest.exists()


def test_retention_with_fewer_pairs_than_the_limit_deletes_nothing(tmp_path: Path) -> None:
    pairs = [make_paths(tmp_path, second) for second in (1, 2)]
    for pair in pairs:
        save_pair(pair)

    apply_retention(pairs[0].handoff.parent, keep=3)

    assert all(pair.handoff.exists() and pair.digest.exists() for pair in pairs)


def test_retention_counts_a_digest_without_handoff_as_a_pair(tmp_path: Path) -> None:
    older = make_paths(tmp_path, 1)
    newer = make_paths(tmp_path, 2)
    save_pair(older)
    write_digest(newer, "digest only")

    apply_retention(older.handoff.parent, keep=1)

    assert newer.digest.exists()
    assert not older.handoff.exists()
    assert not older.digest.exists()


def test_retention_leaves_files_that_are_not_handoff_pairs(tmp_path: Path) -> None:
    pairs = [make_paths(tmp_path, second) for second in (1, 2)]
    for pair in pairs:
        save_pair(pair)
    folder = pairs[0].handoff.parent
    foreign = [
        folder / "notes.md",
        folder / "20260501-123001.md",
        folder / "handoff.log",
        folder / "20260501-123001-0f3a9c21.txt",
    ]
    for path in foreign:
        path.write_text("keep me", encoding="utf-8")

    apply_retention(folder, keep=1)

    assert all(path.exists() for path in foreign)
    assert not pairs[0].handoff.exists()


def test_retention_does_not_touch_other_projects(tmp_path: Path) -> None:
    mine = [make_paths(tmp_path, second, tmp_path / "a") for second in (1, 2)]
    other = make_paths(tmp_path, 1, tmp_path / "b")
    for pair in (*mine, other):
        save_pair(pair)

    apply_retention(mine[0].handoff.parent, keep=1)

    assert other.handoff.exists()
    assert other.digest.exists()


def test_retention_on_a_missing_folder_does_nothing(tmp_path: Path) -> None:
    apply_retention(tmp_path / "absent", keep=3)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_saved_files_are_readable_only_by_their_owner(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)

    save_pair(paths)

    assert mode_of(paths.handoff) == 0o600
    assert mode_of(paths.digest) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_project_and_base_folders_are_private(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)

    save_pair(paths)

    assert mode_of(paths.handoff.parent) == 0o700
    assert mode_of(tmp_path / "base") == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_existing_open_folders_and_files_are_tightened(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    paths.handoff.parent.mkdir(parents=True, mode=0o755)
    (tmp_path / "base").chmod(0o755)
    paths.handoff.write_text("old", encoding="utf-8")
    paths.handoff.chmod(0o644)

    write_handoff(paths, "new")

    assert mode_of(paths.handoff.parent) == 0o700
    assert mode_of(tmp_path / "base") == 0o700
    assert mode_of(paths.handoff) == 0o600
    assert paths.handoff.read_text(encoding="utf-8") == "new"
