import json
import os
import sys
from pathlib import Path

import pytest

from session_handoff.haiku import HaikuError, run_haiku

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake claude is a shebang script, which Windows cannot execute from PATH",
)

FAKE_CLAUDE = """\
#!{python}
import json, os, sys, time

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
with open(os.environ["FAKE_CLAUDE_RECORD"], "w", encoding="utf-8") as record:
    json.dump(
        {{
            "argv": sys.argv[1:],
            "env": dict(os.environ),
            "cwd": os.getcwd(),
            "stdin": sys.stdin.read(),
        }},
        record,
    )
if mode == "sleep":
    time.sleep(10)
elif mode == "exit1":
    sys.stderr.write("login expired")
    sys.exit(1)
elif mode == "garbage":
    print("not json at all")
elif mode == "no_result":
    print(json.dumps({{"is_error": False}}))
elif mode == "is_error":
    print(json.dumps({{"is_error": True, "result": "rate limited"}}))
else:
    print(json.dumps({{"is_error": False, "result": "# Handoff: from haiku"}}))
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Puts a recording `claude` first on PATH and returns the file it records into."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    script.chmod(0o755)
    record = tmp_path / "record.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    return record


def recorded(record: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(record.read_text(encoding="utf-8"))
    return loaded


def test_returns_the_result_text_of_the_json_answer(fake_claude: Path) -> None:
    assert run_haiku("the digest", "the prompt") == "# Handoff: from haiku"


def test_passes_the_digest_on_stdin(fake_claude: Path) -> None:
    run_haiku("the digest, with ñ", "the prompt")

    assert recorded(fake_claude)["stdin"] == "the digest, with ñ"


def test_the_cli_is_called_with_the_flags_that_keep_the_call_isolated_and_cheap(
    fake_claude: Path,
) -> None:
    run_haiku("digest", "the system prompt")

    argv = recorded(fake_claude)["argv"]
    assert isinstance(argv, list)
    for flag, value in [
        ("--model", "haiku"),
        ("--tools", ""),
        ("--setting-sources", ""),
        ("--output-format", "json"),
        ("--system-prompt", "the system prompt"),
    ]:
        assert argv[argv.index(flag) + 1] == value
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in argv


def test_the_system_prompt_is_the_value_after_its_flag_and_the_digest_is_not_an_argument(
    fake_claude: Path,
) -> None:
    run_haiku("the digest", "the system prompt")

    argv = recorded(fake_claude)["argv"]
    assert isinstance(argv, list)
    assert argv[-2:] == ["--system-prompt", "the system prompt"]
    assert "the digest" not in argv


def test_credentials_and_routing_for_other_providers_are_not_passed_but_the_rest_is(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stripped = [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    ]
    for name in stripped:
        monkeypatch.setenv(name, "should-not-leak")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription-token")
    monkeypatch.setenv("UNRELATED_SETTING", "kept")

    run_haiku("digest", "prompt")

    env = recorded(fake_claude)["env"]
    assert isinstance(env, dict)
    for name in stripped:
        assert name not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "subscription-token"
    assert env["UNRELATED_SETTING"] == "kept"


def test_the_caller_environment_is_left_untouched(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-still-here")

    run_haiku("digest", "prompt")

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-still-here"


def test_runs_outside_the_project_in_a_directory_that_is_cleaned_up(
    fake_claude: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    run_haiku("digest", "prompt")

    cwd = Path(str(recorded(fake_claude)["cwd"]))
    assert cwd.resolve() != project.resolve()
    assert not cwd.resolve().is_relative_to(project.resolve())
    assert not cwd.exists()


def test_missing_executable_is_a_clear_error(fake_claude: Path) -> None:
    with pytest.raises(HaikuError, match="not found"):
        run_haiku("digest", "prompt", executable="claude-that-does-not-exist")


def test_timeout_is_a_clear_error(fake_claude: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")

    with pytest.raises(HaikuError, match="timed out"):
        run_haiku("digest", "prompt", timeout_seconds=0.5)


def test_non_zero_exit_reports_the_code_and_stderr(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "exit1")

    with pytest.raises(HaikuError, match="exit code 1") as error:
        run_haiku("digest", "prompt")

    assert "login expired" in str(error.value)


@pytest.mark.parametrize("mode", ["garbage", "no_result"])
def test_output_that_is_not_the_expected_json_is_a_clear_error(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)

    with pytest.raises(HaikuError, match="unexpected output"):
        run_haiku("digest", "prompt")


def test_is_error_true_raises_with_the_reported_message(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "is_error")

    with pytest.raises(HaikuError, match="rate limited"):
        run_haiku("digest", "prompt")
