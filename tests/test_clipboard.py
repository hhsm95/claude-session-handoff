import sys
from pathlib import Path

import pytest

from session_handoff.clipboard import copy_to_clipboard

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake pbcopy is a shebang script, which Windows cannot execute",
)


def _fake_pbcopy(directory: Path, body: str) -> Path:
    script = directory / "pbcopy"
    script.write_text(f"#!{sys.executable}\nimport sys\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_copies_the_text_through_pbcopy(tmp_path: Path) -> None:
    target = tmp_path / "clipboard.txt"
    script = _fake_pbcopy(tmp_path, f"open({str(target)!r}, 'w').write(sys.stdin.read())")

    copied = copy_to_clipboard("@/some/path.md", find_command=lambda _name: str(script))

    assert copied is True
    assert target.read_text(encoding="utf-8") == "@/some/path.md"


def test_reports_false_when_pbcopy_is_missing() -> None:
    assert copy_to_clipboard("text", find_command=lambda _name: None) is False


def test_reports_false_when_pbcopy_exits_with_an_error(tmp_path: Path) -> None:
    script = _fake_pbcopy(tmp_path, "sys.exit(1)")

    assert copy_to_clipboard("text", find_command=lambda _name: str(script)) is False


def test_reports_false_when_the_command_cannot_be_executed(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assert copy_to_clipboard("text", find_command=lambda _name: str(missing)) is False
