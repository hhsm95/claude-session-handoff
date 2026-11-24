import pytest

from session_handoff.command import HandoffCommand, parse_command


@pytest.mark.parametrize(
    "prompt",
    ["/handoff", "/session-handoff:handoff", "   /handoff", "\t/handoff\n", "/handoff  "],
)
def test_bare_command_has_no_focus_and_no_flag(prompt: str) -> None:
    assert parse_command(prompt) == HandoffCommand(force_haiku=False, focus="")


def test_arguments_become_the_focus_with_whitespace_normalized() -> None:
    command = parse_command("/handoff   continue  with\nthe parser ")

    assert command == HandoffCommand(force_haiku=False, focus="continue with the parser")


@pytest.mark.parametrize(
    ("prompt", "expected_focus"),
    [
        ("/handoff --haiku", ""),
        ("/handoff --haiku continue with the parser", "continue with the parser"),
        ("/handoff continue --haiku with the parser", "continue with the parser"),
        ("/session-handoff:handoff continue with the parser --haiku", "continue with the parser"),
    ],
)
def test_haiku_flag_anywhere_forces_cold_and_leaves_the_focus(
    prompt: str, expected_focus: str
) -> None:
    assert parse_command(prompt) == HandoffCommand(force_haiku=True, focus=expected_focus)


def test_flag_lookalike_stays_in_the_focus() -> None:
    command = parse_command("/handoff --haikus now")

    assert command == HandoffCommand(force_haiku=False, focus="--haikus now")


@pytest.mark.parametrize(
    "prompt",
    ["/handoffx", "please /handoff", "", "hello", "/session-handoff:handoffs", "//handoff"],
)
def test_other_prompts_are_not_the_command(prompt: str) -> None:
    assert parse_command(prompt) is None
