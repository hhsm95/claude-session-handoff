"""Secret redaction and mention neutralizing applied to the digest before it is saved or sent."""

import re

from session_handoff.constants import MENTION_GUARD, REDACTED_PLACEHOLDER

# An `@` right after a word character is part of an email or a `git@host` URL, not a mention.
_MENTION = re.compile(rf"(?<!\w)@(?=[^\s{MENTION_GUARD}])")
_SECRET_ASSIGNMENT = re.compile(
    r"""(?P<name>\b[\w.-]*(?:KEY|TOKEN|SECRET|PASSWORD)=)(?:"[^"]*"|'[^']*'|\S+)""",
    re.IGNORECASE,
)
_BEARER_HEADER = re.compile(r"(?P<prefix>\bBearer\s+)[\w.~+/=-]+", re.IGNORECASE)
_TOKEN_PREFIXES = re.compile(r"\b(?:sk-|ghp_|glpat-)[\w-]+")


def neutralize_mentions(text: str) -> str:
    """Break every `@path` mention so `claude -p` does not attach the file to its input."""
    return _MENTION.sub(f"@{MENTION_GUARD}", text)


def redact_secrets(text: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(rf"\g<name>{REDACTED_PLACEHOLDER}", text)
    text = _BEARER_HEADER.sub(rf"\g<prefix>{REDACTED_PLACEHOLDER}", text)
    return _TOKEN_PREFIXES.sub(REDACTED_PLACEHOLDER, text)
