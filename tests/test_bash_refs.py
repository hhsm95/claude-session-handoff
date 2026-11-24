from pathlib import Path

import pytest

from session_handoff.bash_refs import files_in_command


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("a", encoding="utf-8")
    (root / "src" / "b.py").write_text("b", encoding="utf-8")
    return root


def test_a_file_read_through_a_pipeline_is_found(project: Path) -> None:
    assert files_in_command("sed -n '1,40p' src/a.py | head", project) == ("src/a.py",)


def test_several_files_keep_their_order(project: Path) -> None:
    assert files_in_command("diff src/b.py src/a.py", project) == ("src/b.py", "src/a.py")


def test_a_file_named_twice_counts_once(project: Path) -> None:
    assert files_in_command("diff src/a.py src/a.py", project) == ("src/a.py",)


def test_an_absolute_path_inside_the_project_is_reported_relative(project: Path) -> None:
    # Bash commands spell paths with forward slashes, also under Git Bash on Windows.
    command = f"cat {(project / 'src' / 'a.py').as_posix()}"

    assert files_in_command(command, project) == ("src/a.py",)


def test_missing_files_and_paths_outside_the_project_do_not_count(
    project: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    command = f'grep -rn "def main" src/missing.py {outside} ../outside.txt'

    assert files_in_command(command, project) == ()


def test_directories_and_flags_do_not_count(project: Path) -> None:
    assert files_in_command("ls -la src --color=auto", project) == ()


def test_quoted_paths_and_commands_after_separators_are_found(project: Path) -> None:
    (project / "my notes.md").write_text("n", encoding="utf-8")
    command = "cd . && wc -l 'my notes.md'; rtk read src/b.py 2>&1"

    assert files_in_command(command, project) == ("my notes.md", "src/b.py")


@pytest.fixture
def runner(project: Path) -> Path:
    """An executable script in the project, so it exists as a file."""
    script = project / "tools" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    return script


def test_the_program_being_run_does_not_count_but_its_arguments_do(
    project: Path, runner: Path
) -> None:
    assert files_in_command("tools/run.sh src/a.py", project) == ("src/a.py",)


def test_a_script_passed_as_an_argument_still_counts(project: Path, runner: Path) -> None:
    assert files_in_command("bash tools/run.sh", project) == ("tools/run.sh",)


def test_the_program_after_each_control_operator_does_not_count(
    project: Path, runner: Path
) -> None:
    command = "cat src/a.py | ./tools/run.sh src/b.py"

    assert files_in_command(command, project) == ("src/a.py", "src/b.py")
    for operator in ("||", "&&", ";", "&", "(", "\n"):
        assert files_in_command(f"cat src/a.py {operator} tools/run.sh", project) == ("src/a.py",)


def test_environment_assignments_before_the_program_are_skipped(
    project: Path, runner: Path
) -> None:
    assert files_in_command("FOO=bar BAZ=1 tools/run.sh src/a.py", project) == ("src/a.py",)


def test_a_newline_inside_quotes_does_not_start_a_new_command(project: Path, runner: Path) -> None:
    command = "echo 'one\ntwo' tools/run.sh"

    assert files_in_command(command, project) == ("tools/run.sh",)


def test_an_unparsable_command_counts_nothing(project: Path) -> None:
    assert files_in_command("cat 'src/a.py", project) == ()


def test_a_symlink_that_leaves_the_project_does_not_count(project: Path, tmp_path: Path) -> None:
    target = tmp_path / "secret.txt"
    target.write_text("s", encoding="utf-8")
    (project / "link.txt").symlink_to(target)

    assert files_in_command("cat link.txt", project) == ()


def test_a_symlink_loop_does_not_hide_the_real_files_named_with_it(project: Path) -> None:
    (project / "loopa").symlink_to("loopb")
    (project / "loopb").symlink_to("loopa")

    assert files_in_command("cat loopa src/a.py", project) == ("src/a.py",)


def test_a_token_with_a_nul_byte_does_not_hide_the_real_files_named_with_it(
    project: Path,
) -> None:
    assert files_in_command("cat bad\x00name src/a.py", project) == ("src/a.py",)


def test_a_name_too_long_for_the_filesystem_does_not_hide_the_real_files(project: Path) -> None:
    assert files_in_command(f"cat {'x' * 5000} src/a.py", project) == ("src/a.py",)


def test_file_descriptor_numbers_in_redirections_are_not_files(project: Path) -> None:
    (project / "1").write_text("one", encoding="utf-8")
    (project / "2").write_text("two", encoding="utf-8")
    (project / "10").write_text("ten", encoding="utf-8")

    assert files_in_command("python src/a.py 2>&1", project) == ("src/a.py",)
    assert files_in_command("python src/a.py 10>&1", project) == ("src/a.py",)
