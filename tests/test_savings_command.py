import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import write_compacted_session
from session_handoff.entry import dispatch_hook, run_hook
from session_handoff.hook_runtime import HookDependencies
from session_handoff.ledger import read_ledger

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def never_called(_digest: str, _system_prompt: str) -> str:
    raise AssertionError("the savings command must not call Haiku")


def never_copied(_text: str) -> bool:
    raise AssertionError("the savings command must not touch the clipboard")


def run_savings(tmp_path: Path, prompt: str = "/session-handoff:savings", *, base: Path) -> str:
    stdout = io.StringIO()
    dependencies = HookDependencies(
        now=lambda: NOW,
        base_dir=base,
        run_haiku=never_called,
        copy_to_clipboard=never_copied,
        projects_dir=tmp_path / "projects",
    )
    run_hook(
        json.dumps({"prompt": prompt, "session_id": "live", "cwd": str(tmp_path)}),
        stdout,
        dependencies,
    )
    return stdout.getvalue()


def blocked_reason(output: str) -> str:
    answer = json.loads(output)
    assert answer["decision"] == "block"
    assert answer["hookSpecificOutput"]["suppressOriginalPrompt"] is True
    reason: str = answer["reason"]
    return reason


@pytest.mark.parametrize("prompt", ["/savings", "/session-handoff:savings"])
def test_the_command_back_fills_blocks_and_reports(tmp_path: Path, prompt: str) -> None:
    base = tmp_path / "handoffs"
    write_compacted_session(tmp_path / "projects" / "-p" / "a.jsonl", boundary_uuid="b1")
    write_compacted_session(
        tmp_path / "projects" / "-p" / "b.jsonl", boundary_uuid="b2", reply_tokens=None
    )

    reason = blocked_reason(run_savings(tmp_path, prompt, base=base))

    assert "Compaction savings (2 compactions: 1 measured, 1 unused)" in reason
    assert "Saved: 90,000 tokens (90.0%)" in reason
    assert len(read_ledger(base)) == 2


def test_an_empty_ledger_still_blocks_with_the_empty_message(tmp_path: Path) -> None:
    reason = blocked_reason(run_savings(tmp_path, base=tmp_path / "handoffs"))

    assert "no compactions found" in reason.lower()


def test_a_failure_still_ends_in_a_block(tmp_path: Path) -> None:
    write_compacted_session(tmp_path / "projects" / "-p" / "a.jsonl")
    not_a_folder = tmp_path / "file"
    not_a_folder.write_text("", encoding="utf-8")

    reason = blocked_reason(run_savings(tmp_path, base=not_a_folder / "handoffs"))

    assert reason.startswith("Savings report failed:")


def test_the_event_dispatcher_routes_the_command_too(tmp_path: Path) -> None:
    stdout = io.StringIO()
    dependencies = HookDependencies(
        now=lambda: NOW,
        base_dir=tmp_path / "handoffs",
        run_haiku=never_called,
        copy_to_clipboard=never_copied,
        projects_dir=tmp_path / "projects",
    )

    dispatch_hook(json.dumps({"prompt": "/savings"}), stdout, dependencies)

    assert blocked_reason(stdout.getvalue())
