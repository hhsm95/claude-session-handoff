import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, TypedDict, Unpack

import pytest

from conftest import GIT_WORST_CASE_COMMANDS
from session_handoff.constants import (
    GIT_BRANCH_COMMAND,
    GIT_GLOBAL_ARGS,
    GIT_SHORT_HEAD_COMMAND,
    GIT_UNAVAILABLE_PLACEHOLDER,
    NO_GIT_MESSAGE,
)
from session_handoff.git_info import collect_git_info


class RunKeywords(TypedDict):
    """The keyword arguments `git_info` passes to `subprocess.run`."""

    cwd: Path
    capture_output: bool
    text: Literal[True]
    encoding: str
    errors: str
    timeout: float
    check: bool


def record_git_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Run git for real while remembering the full argv of every call."""
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(command: list[str], **kwargs: Unpack[RunKeywords]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


def fail_git_command(monkeypatch: pytest.MonkeyPatch, failing: tuple[str, ...]) -> None:
    """Make the git command whose arguments are exactly `failing` exit with an error."""
    real_run = subprocess.run

    def run(command: list[str], **kwargs: Unpack[RunKeywords]) -> subprocess.CompletedProcess[str]:
        if tuple(command[2:]) == failing:
            return subprocess.CompletedProcess(command, 128, stdout="", stderr="fatal")
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"add {name}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "work-branch")
    return tmp_path


def test_outside_a_repository_reports_no_git_repository(tmp_path: Path) -> None:
    assert collect_git_info(tmp_path) == NO_GIT_MESSAGE


def test_missing_directory_reports_no_git_repository(tmp_path: Path) -> None:
    assert collect_git_info(tmp_path / "does-not-exist") == NO_GIT_MESSAGE


def test_branch_recent_commits_and_status_are_reported(repo: Path) -> None:
    commit(repo, "first.txt")
    (repo / "untracked.txt").write_text("x", encoding="utf-8")

    info = collect_git_info(repo)

    assert "work-branch" in info
    assert "add first.txt" in info
    assert "?? untracked.txt" in info


def test_only_the_most_recent_commits_are_listed(repo: Path) -> None:
    for index in range(4):
        commit(repo, f"file{index}.txt")

    info = collect_git_info(repo, recent_commits=2)

    assert "add file3.txt" in info
    assert "add file2.txt" in info
    assert "add file1.txt" not in info


def test_clean_repository_says_so(repo: Path) -> None:
    commit(repo, "first.txt")

    assert "clean" in collect_git_info(repo)


def test_repository_without_commits_still_reports_the_branch(repo: Path) -> None:
    info = collect_git_info(repo)

    assert "work-branch" in info


def test_output_is_capped_to_the_line_limit(repo: Path) -> None:
    commit(repo, "first.txt")
    for index in range(10):
        (repo / f"u{index}.txt").write_text("x", encoding="utf-8")

    info = collect_git_info(repo, max_lines=6)

    assert len(info.splitlines()) == 6


def test_git_that_times_out_before_answering_is_reported_as_no_repository(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: list[object] = []

    def hang(*_args: object, **kwargs: object) -> None:
        timeouts.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd="git", timeout=0)

    monkeypatch.setattr(subprocess, "run", hang)

    assert collect_git_info(repo, timeout_seconds=0.25) == NO_GIT_MESSAGE
    assert timeouts == [0.25]


def test_a_git_call_never_takes_the_optional_index_lock(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = record_git_calls(monkeypatch)

    collect_git_info(repo)

    assert calls
    assert all(command[:2] == ["git", *GIT_GLOBAL_ARGS] for command in calls)


def test_a_detached_head_runs_exactly_the_documented_worst_case_commands(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(repo, "first.txt")
    git(repo, "checkout", "--detach")
    calls = record_git_calls(monkeypatch)

    collect_git_info(repo, recent_commits=5)

    assert calls == [["git", *GIT_GLOBAL_ARGS, *command] for command in GIT_WORST_CASE_COMMANDS]


FAKE_GIT = """\
#!{python}
import os, sys, time

arguments = sys.argv[1:]
if "{subcommand}" in arguments:
{misbehaviour}
os.execv({real_git!r}, [{real_git!r}, *arguments])
"""
SLEEP = "    time.sleep(30)"
INVALID_UTF8 = "    sys.stdout.buffer.write(b'?? caf\\xe9.txt\\n'); sys.exit(0)"


def install_fake_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, subcommand: str, misbehaviour: str
) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake = fake_bin / "git"
    fake.write_text(
        FAKE_GIT.format(
            python=sys.executable,
            subcommand=subcommand,
            misbehaviour=misbehaviour,
            real_git=real_git,
        ),
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.skipif(sys.platform == "win32", reason="the fake git is a shebang script")
def test_a_git_command_that_times_out_shows_as_unavailable_not_as_clean(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(repo, "first.txt")
    install_fake_git(
        tmp_path_factory.mktemp("fake"), monkeypatch, subcommand="status", misbehaviour=SLEEP
    )

    info = collect_git_info(repo, timeout_seconds=1)

    assert info.splitlines()[-2:] == ["Status:", GIT_UNAVAILABLE_PLACEHOLDER]
    assert "add first.txt" in info


@pytest.mark.skipif(sys.platform == "win32", reason="the fake git is a shebang script")
def test_git_output_that_is_not_utf8_is_decoded_without_raising(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(repo, "first.txt")
    install_fake_git(
        tmp_path_factory.mktemp("fake"), monkeypatch, subcommand="status", misbehaviour=INVALID_UTF8
    )

    info = collect_git_info(repo)

    assert "?? caf" in info
    assert GIT_UNAVAILABLE_PLACEHOLDER not in info


def test_a_failing_log_shows_as_unavailable_not_as_none(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_git_command(monkeypatch, GIT_WORST_CASE_COMMANDS[1])

    info = collect_git_info(repo)

    assert info.splitlines()[1:3] == ["Recent commits:", GIT_UNAVAILABLE_PLACEHOLDER]


def test_a_failing_branch_lookup_shows_the_branch_as_unavailable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(repo, "first.txt")
    fail_git_command(monkeypatch, GIT_BRANCH_COMMAND)

    info = collect_git_info(repo)

    assert info.splitlines()[0] == f"Branch: {GIT_UNAVAILABLE_PLACEHOLDER}"


def test_a_failing_short_hash_lookup_on_a_detached_head_shows_the_branch_as_unavailable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(repo, "first.txt")
    git(repo, "checkout", "--detach")
    fail_git_command(monkeypatch, GIT_SHORT_HEAD_COMMAND)

    info = collect_git_info(repo)

    assert info.splitlines()[0] == f"Branch: {GIT_UNAVAILABLE_PLACEHOLDER}"
