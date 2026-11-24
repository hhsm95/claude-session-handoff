"""The handoff document: the Haiku instructions, the check of its answer and the final assembly."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from session_handoff.constants import (
    BRANCH_HEADING,
    DIGEST_LINE_TEMPLATE,
    HANDOFF_COLD_RULES,
    HANDOFF_FORMAT_INSTRUCTIONS,
    HANDOFF_MAX_CHARS,
    HANDOFF_MODEL_SECTIONS,
    HANDOFF_PRIORITY_INSTRUCTIONS,
    HANDOFF_PROMPT_INTRO,
    HANDOFF_REQUIRED_SECTIONS,
    HANDOFF_TITLE_PREFIX,
    INVALID_SECTION_TEMPLATE,
    INVALID_TITLE_TEMPLATE,
    KEY_FILES_HEADING,
    KEY_FILES_MAX,
    KEY_FILES_OMITTED_NOTE,
    MODEL_DOCUMENT_MAX_CHARS,
    NONE_PLACEHOLDER,
    REPOSITORY_STATE_HEADING,
    SECTION_SEPARATOR,
    USER_PRIORITY_TEMPLATE,
)
from session_handoff.digest import FileActivity, render_file_line
from session_handoff.redact import neutralize_mentions, redact_secrets

_OUTER_FENCE = re.compile(r"```[A-Za-z]*\n(?P<body>.*)\n```", re.DOTALL)


@dataclass(frozen=True)
class HandoffExtras:
    """What code, not the model, contributes to the handoff."""

    activity: Sequence[FileActivity]
    git_text: str
    digest_path: Path


class InvalidHandoffError(Exception):
    """The model's answer is not a usable handoff."""


def format_instructions(focus: str, max_chars: int = MODEL_DOCUMENT_MAX_CHARS) -> str:
    """Format, limits and user priority: what any writer of the handoff must be told."""
    layout = "\n".join(
        [f"{HANDOFF_TITLE_PREFIX}: <topic>", *(f"## {name}" for name in HANDOFF_MODEL_SECTIONS)]
    )
    parts = [HANDOFF_FORMAT_INSTRUCTIONS.format(format=layout, max_chars=max_chars)]
    if focus:
        parts.append(USER_PRIORITY_TEMPLATE.format(focus=focus))
        parts.append(HANDOFF_PRIORITY_INSTRUCTIONS)
    return "\n".join(parts)


def build_system_prompt(focus: str, max_chars: int = MODEL_DOCUMENT_MAX_CHARS) -> str:
    return (
        f"{HANDOFF_PROMPT_INTRO}\n{HANDOFF_COLD_RULES}\n\n{format_instructions(focus, max_chars)}"
    )


def build_haiku_input(git_text: str, digest: str) -> str:
    """What Haiku reads: the repository state computed by code, then the digest.

    The saved digest file does not include the repository state; the handoff carries it in
    `Branch / commits`. The digest is already redacted and neutralized, so the git text gets the
    same treatment here.
    """
    state = neutralize_mentions(redact_secrets(git_text))
    return f"{REPOSITORY_STATE_HEADING}\n{state}{SECTION_SEPARATOR}{digest}"


def validate_model_answer(answer: str) -> str:
    """The stripped answer, or `InvalidHandoffError` if it lacks the title or a required section."""
    text = _without_outer_fence(answer.strip())
    if not text.startswith(HANDOFF_TITLE_PREFIX):
        raise InvalidHandoffError(INVALID_TITLE_TEMPLATE.format(title=HANDOFF_TITLE_PREFIX))
    for name in HANDOFF_REQUIRED_SECTIONS:
        if re.search(rf"^## {re.escape(name)}\s*$", text, re.MULTILINE) is None:
            raise InvalidHandoffError(INVALID_SECTION_TEMPLATE.format(section=name))
    return text


def _without_outer_fence(text: str) -> str:
    """Models sometimes wrap the whole document in one markdown fence; only that one is removed."""
    fenced = _OUTER_FENCE.fullmatch(text)
    return fenced["body"].strip() if fenced else text


def render_key_files(activity: Sequence[FileActivity], *, limit: int) -> str:
    """The `limit` highest-scoring files, best first; ties keep their order of appearance.

    The score weighs edits and writes above reads and reads above bash refs; the lines show the
    raw counts.
    """
    if not activity:
        return f"{KEY_FILES_HEADING}\n{NONE_PLACEHOLDER}"
    if limit <= 0:
        return f"{KEY_FILES_HEADING}\n{KEY_FILES_OMITTED_NOTE}"
    busiest = sorted(activity, key=lambda item: item.score, reverse=True)[:limit]
    return "\n".join([KEY_FILES_HEADING, *(render_file_line(item) for item in busiest)])


def render_deterministic_sections(
    extras: HandoffExtras, *, key_files_limit: int = KEY_FILES_MAX
) -> str:
    """`Key files`, `Branch / commits` and the digest line: everything that code writes."""
    return SECTION_SEPARATOR.join(
        [
            render_key_files(extras.activity, limit=key_files_limit),
            f"{BRANCH_HEADING}\n{extras.git_text}",
            DIGEST_LINE_TEMPLATE.format(digest_path=extras.digest_path),
        ]
    )


def assemble_handoff(
    model_text: str,
    extras: HandoffExtras,
    *,
    max_chars: int = HANDOFF_MAX_CHARS,
    key_files_limit: int = KEY_FILES_MAX,
) -> str:
    """Model text plus the deterministic sections, within `max_chars`.

    Over the cap, key files go first, then the end of the model text; the digest line stays.
    If the deterministic sections alone exceed the cap they are returned whole.
    """
    model = model_text.strip()
    for limit in range(min(len(extras.activity), key_files_limit), -1, -1):
        tail = render_deterministic_sections(extras, key_files_limit=limit)
        document = f"{model}{SECTION_SEPARATOR}{tail}"
        if len(document) <= max_chars:
            return document
    budget = max_chars - len(SECTION_SEPARATOR) - len(tail)
    kept = model[: max(budget, 0)].rstrip()
    return f"{kept}{SECTION_SEPARATOR}{tail}" if kept else tail
