import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from session_handoff.entry import dispatch_hook
from session_handoff.haiku import HaikuError
from session_handoff.hook_runtime import HookDependencies


def unused_haiku(_digest: str, _system_prompt: str) -> str:
    raise HaikuError("must not be called")


def dispatch(tmp_path: Path, payload: dict[str, object]) -> str:
    stdout = io.StringIO()
    dependencies = HookDependencies(
        now=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        base_dir=tmp_path / "handoffs",
        run_haiku=unused_haiku,
        copy_to_clipboard=lambda _text: False,
    )
    dispatch_hook(json.dumps(payload), stdout, dependencies)
    return stdout.getvalue()


@pytest.mark.parametrize("event", ["SessionStart", "", "userpromptsubmit"])
def test_an_unknown_event_exits_silently_even_with_a_command_prompt(
    tmp_path: Path, event: str
) -> None:
    output = dispatch(tmp_path, {"hook_event_name": event, "prompt": "/handoff"})

    assert output == ""
    assert not (tmp_path / "handoffs").exists()


def test_a_non_string_event_exits_silently(tmp_path: Path) -> None:
    assert dispatch(tmp_path, {"hook_event_name": 7, "prompt": "/handoff"}) == ""


def test_invalid_json_exits_silently(tmp_path: Path) -> None:
    stdout = io.StringIO()
    dependencies = HookDependencies(
        now=lambda: datetime(2026, 5, 1, tzinfo=UTC),
        base_dir=tmp_path,
        run_haiku=unused_haiku,
        copy_to_clipboard=lambda _text: False,
    )

    dispatch_hook("not json", stdout, dependencies)

    assert stdout.getvalue() == ""


def test_a_user_prompt_submit_event_runs_the_handoff_pipeline(tmp_path: Path) -> None:
    output = dispatch(
        tmp_path,
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/handoff",
            "session_id": "s1",
            "transcript_path": str(tmp_path / "missing.jsonl"),
            "cwd": str(tmp_path),
        },
    )

    assert json.loads(output)["decision"] == "block"


def test_a_payload_without_an_event_name_is_a_user_prompt(tmp_path: Path) -> None:
    output = dispatch(
        tmp_path,
        {
            "prompt": "/handoff",
            "session_id": "s1",
            "transcript_path": str(tmp_path / "missing.jsonl"),
            "cwd": str(tmp_path),
        },
    )

    assert json.loads(output)["decision"] == "block"
