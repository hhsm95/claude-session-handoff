import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from conftest import VALID_HANDOFF_TEXT, TranscriptWriter
from session_handoff.constants import COMMAND_PATTERN, PLUGIN_VERSION

HOOK_SCRIPT = Path(__file__).resolve().parent.parent / "hooks" / "run.py"

FAKE_CLAUDE = """\
#!{python}
import json, sys

sys.stdin.read()
print(json.dumps({{"result": {answer!r}, "is_error": False}}))
"""

# Shadows the real pbcopy so the suite never overwrites the developer's clipboard.
FAKE_PBCOPY = """\
#!{python}
import sys

with open({record!r}, "w", encoding="utf-8") as handle:
    handle.write(sys.stdin.read())
"""


def install_fake(bin_dir: Path, name: str, source: str) -> None:
    script = bin_dir / name
    script.write_text(source, encoding="utf-8")
    script.chmod(0o755)


def run_hook_script(
    payload: dict[str, str], *, home: Path | None = None, path_prefix: Path | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    if home is not None:
        environment["HOME"] = str(home)
    if path_prefix is not None:
        environment["PATH"] = f"{path_prefix}{os.pathsep}{environment['PATH']}"
    return subprocess.run(
        [sys.executable, "-I", str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_hook_ignores_a_prompt_that_is_not_the_command() -> None:
    result = run_hook_script({"prompt": "hello"})

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake claude is a shebang script, which Windows cannot execute from PATH",
)
def test_hook_script_delivers_a_cold_handoff_end_to_end(
    tmp_path: Path, write_transcript: TranscriptWriter
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    install_fake(
        fake_bin, "claude", FAKE_CLAUDE.format(python=sys.executable, answer=VALID_HANDOFF_TEXT)
    )
    install_fake(
        fake_bin,
        "pbcopy",
        FAKE_PBCOPY.format(python=sys.executable, record=str(tmp_path / "clipboard.txt")),
    )
    project = tmp_path / "project"
    project.mkdir()
    transcript = write_transcript(datetime.now(UTC) - timedelta(days=1), cache_1h=False)

    result = run_hook_script(
        {
            "prompt": "/session-handoff:handoff",
            "session_id": "0f3a9c21-aaaa",
            "transcript_path": str(transcript),
            "cwd": str(project),
        },
        home=tmp_path,
        path_prefix=fake_bin,
    )

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["decision"] == "block"
    resume_line = output["reason"].splitlines()[-1]
    assert resume_line.startswith("@")
    handoff = Path(resume_line.removeprefix("@"))
    assert handoff.is_relative_to(tmp_path / ".claude" / "handoffs")
    digest_line = f"Full detail (read only if needed): {handoff.with_suffix('.digest.md')}"
    assert handoff.read_text(encoding="utf-8").endswith(digest_line)
    # The resume line reached the fake pbcopy, never the developer's real clipboard.
    assert (tmp_path / "clipboard.txt").read_text(encoding="utf-8") == resume_line


# --- the hook script itself failing -------------------------------------------------------------

BLOCKING_EXIT_CODE = 2


def load_run_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hook_run_script", HOOK_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def broken_plugin_root(tmp_path: Path) -> Path:
    """A plugin copy whose package raises when imported."""
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    shutil.copy(HOOK_SCRIPT, root / "hooks" / "run.py")
    package = root / "src" / "session_handoff"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "entry.py").write_text("raise RuntimeError('broken install')\n", encoding="utf-8")
    return root


def run_broken(root: Path, prompt: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(root / "hooks" / "run.py")],
        input=json.dumps({"prompt": prompt}),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("prompt", ["/handoff", "/session-handoff:handoff --haiku focus"])
def test_the_command_is_blocked_with_exit_2_when_the_package_cannot_load(
    tmp_path: Path, prompt: str
) -> None:
    result = run_broken(broken_plugin_root(tmp_path), prompt)

    assert result.returncode == BLOCKING_EXIT_CODE
    assert "broken install" in result.stderr
    assert result.stdout == ""


def test_other_prompts_pass_silently_when_the_package_cannot_load(tmp_path: Path) -> None:
    result = run_broken(broken_plugin_root(tmp_path), "hello")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_the_script_blocks_the_command_on_an_unsupported_python(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = load_run_script()
    monkeypatch.setattr(script.sys, "version_info", (3, 6, 0))
    monkeypatch.setattr(
        script.sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"prompt": "/handoff"}'))
    )

    with pytest.raises(SystemExit) as exit_info:
        script.main()

    assert exit_info.value.code == BLOCKING_EXIT_CODE
    assert "3.11" in capsys.readouterr().err


def test_the_scripts_command_pattern_is_the_packages() -> None:
    assert load_run_script().COMMAND_PATTERN == COMMAND_PATTERN


def test_the_script_parses_under_an_old_grammar() -> None:
    source = HOOK_SCRIPT.read_text(encoding="utf-8")

    ast.parse(source, feature_version=(3, 6))


def test_a_system_exit_inside_the_pipeline_still_blocks_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = load_run_script()

    def exit_early(_raw_input: str) -> None:
        raise SystemExit(1)

    monkeypatch.setattr(script, "run", exit_early)
    monkeypatch.setattr(
        script.sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"prompt": "/handoff"}'))
    )

    with pytest.raises(SystemExit) as exit_info:
        script.main()

    assert exit_info.value.code == BLOCKING_EXIT_CODE
    assert "failed before it could run (1)" in capsys.readouterr().err


# --- failure policy per event -------------------------------------------------------------------


def run_broken_event(root: Path, payload: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(root / "hooks" / "run.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("prompt", ["/savings", "/session-handoff:savings", "/savings extra"])
def test_the_savings_command_is_blocked_when_the_package_cannot_load(
    tmp_path: Path, prompt: str
) -> None:
    result = run_broken(broken_plugin_root(tmp_path), prompt)

    assert result.returncode == BLOCKING_EXIT_CODE


@pytest.mark.parametrize(
    ("prompt", "named", "other"),
    [
        ("/savings", "/savings", "/handoff"),
        ("/session-handoff:savings", "/savings", "/handoff"),
        ("/handoff focus", "/handoff", "/savings"),
    ],
)
def test_the_failure_message_names_the_command_that_was_blocked(
    tmp_path: Path, prompt: str, named: str, other: str
) -> None:
    result = run_broken(broken_plugin_root(tmp_path), prompt)

    assert f"The {named} prompt was blocked" in result.stderr
    assert other not in result.stderr


@pytest.mark.parametrize("prompt", ["/savingsx", "/session-handoff:savingsx", "savings"])
def test_lookalike_prompts_are_not_blocked_when_the_package_cannot_load(
    tmp_path: Path, prompt: str
) -> None:
    result = run_broken(broken_plugin_root(tmp_path), prompt)

    assert result.returncode == 0


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact", "Stop"])
def test_compaction_and_stop_events_exit_0_silently_when_the_package_cannot_load(
    tmp_path: Path, event: str
) -> None:
    payload = {"hook_event_name": event, "prompt": "/handoff", "session_id": "s1"}

    result = run_broken_event(broken_plugin_root(tmp_path), payload)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_an_explicit_user_prompt_event_is_still_blocked(tmp_path: Path) -> None:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "/handoff"}

    result = run_broken_event(broken_plugin_root(tmp_path), payload)

    assert result.returncode == BLOCKING_EXIT_CODE


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact", "Stop"])
def test_the_script_exits_0_for_a_non_prompt_event_on_an_unsupported_python(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], event: str
) -> None:
    script = load_run_script()
    monkeypatch.setattr(script.sys, "version_info", (3, 6, 0))
    raw = json.dumps({"hook_event_name": event, "prompt": "/handoff"}).encode()
    monkeypatch.setattr(script.sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))

    script.main()  # returns normally: no SystemExit

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": "PreCompact", "prompt": "/handoff", "trigger": "auto"},
        {"hook_event_name": "PostCompact", "prompt": "/savings", "compact_summary": "x"},
        {"hook_event_name": "Stop", "prompt": "/session-handoff:handoff", "session_id": "s1"},
    ],
)
def test_a_failing_pipeline_cannot_block_a_non_prompt_event_even_if_its_prompt_matches(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, str]
) -> None:
    script = load_run_script()

    def fail(_raw_input: str) -> None:
        raise RuntimeError("broken install")

    monkeypatch.setattr(script, "run", fail)
    raw = json.dumps(payload).encode()
    monkeypatch.setattr(script.sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))

    script.main()  # returns normally: no SystemExit


def test_invalid_json_cannot_block(monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_run_script()
    monkeypatch.setattr(script.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{broken")))

    script.main()  # returns normally: no SystemExit


# --- hooks.json ---------------------------------------------------------------------------------

HOOKS_JSON = HOOK_SCRIPT.parent / "hooks.json"
COMPACTION_EVENT_TIMEOUT_SECONDS = 30


def hook_entries() -> dict[str, list[dict[str, object]]]:
    registered = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
    return {event: groups[0]["hooks"] for event, groups in registered.items()}


def test_every_event_runs_the_same_command() -> None:
    entries = hook_entries()

    assert set(entries) == {"UserPromptSubmit", "PreCompact", "PostCompact", "Stop"}
    commands = {hooks[0]["command"] for hooks in entries.values()}
    assert len(commands) == 1


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact", "Stop"])
def test_the_new_events_have_a_30_second_timeout_and_no_matcher(event: str) -> None:
    registered = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]

    assert "matcher" not in registered[event][0]
    assert hook_entries()[event][0]["timeout"] == COMPACTION_EVENT_TIMEOUT_SECONDS


def test_the_plugin_version_constant_matches_the_manifest() -> None:
    manifest = HOOK_SCRIPT.parent.parent / ".claude-plugin" / "plugin.json"

    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]

    assert version == PLUGIN_VERSION
