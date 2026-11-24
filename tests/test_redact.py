import pytest

from session_handoff.constants import MENTION_GUARD, REDACTED_PLACEHOLDER
from session_handoff.redact import neutralize_mentions, redact_secrets


def test_token_assignment_in_a_command_keeps_the_name_and_hides_the_value() -> None:
    assert redact_secrets("export API_TOKEN=abc123") == "export API_TOKEN=[REDACTED]"


@pytest.mark.parametrize("name", ["OPENAI_KEY", "CLIENT_SECRET", "DB_PASSWORD", "github_token"])
def test_secret_assignment_names_are_redacted(name: str) -> None:
    assert redact_secrets(f"{name}=hunter2 next") == f"{name}={REDACTED_PLACEHOLDER} next"


def test_quoted_assignment_value_is_redacted_whole() -> None:
    assert redact_secrets('PASSWORD="two words"') == "PASSWORD=[REDACTED]"


def test_bearer_header_value_is_redacted() -> None:
    redacted = redact_secrets("curl -H 'Authorization: Bearer eyJhbGciOi.abc-def_1'")

    assert "eyJhbGciOi" not in redacted
    assert "Bearer [REDACTED]" in redacted


@pytest.mark.parametrize(
    "secret", ["sk-ant-api03-AbC_123", "ghp_16C7e42F292c6912E7710c838347", "glpat-xYz-12_34"]
)
def test_known_token_prefixes_are_redacted(secret: str) -> None:
    assert redact_secrets(f"token is {secret} ok") == f"token is {REDACTED_PLACEHOLDER} ok"


def test_ordinary_text_is_left_unchanged() -> None:
    text = "risk-averse task-force; KEYBOARD=us; the key is simple"

    assert redact_secrets(text) == text


def test_a_mention_gets_a_word_joiner_so_the_cli_does_not_expand_it() -> None:
    neutralized = neutralize_mentions("see @/abs/file.py and @src/a.py")

    assert neutralized == f"see @{MENTION_GUARD}/abs/file.py and @{MENTION_GUARD}src/a.py"


def test_an_at_sign_that_is_not_a_mention_is_left_alone() -> None:
    text = "ping @ noon, or at the end @"

    assert neutralize_mentions(text) == text


def test_neutralizing_twice_changes_nothing_more() -> None:
    once = neutralize_mentions("@/abs/file")

    assert neutralize_mentions(once) == once


def test_every_occurrence_is_redacted() -> None:
    redacted = redact_secrets("A_KEY=1\nB_KEY=2")

    assert redacted == "A_KEY=[REDACTED]\nB_KEY=[REDACTED]"


def test_emails_and_git_urls_are_not_mentions() -> None:
    text = "mail a@b.com or clone git@github.com:x/y.git"

    assert neutralize_mentions(text) == text


def test_emails_with_digits_and_underscores_are_not_mentions() -> None:
    assert neutralize_mentions("user1@x.com a_b@x.org") == "user1@x.com a_b@x.org"


@pytest.mark.parametrize("mention", ["(@src/a.py", " @file.md", "node_modules/@types/node"])
def test_a_mention_after_a_non_word_character_is_still_guarded(mention: str) -> None:
    assert neutralize_mentions(mention) == mention.replace("@", f"@{MENTION_GUARD}")
