"""Render the main-thread entries as a compact, redacted markdown digest."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from session_handoff.bash_refs import files_in_command
from session_handoff.constants import (
    ACTIVITY_WEIGHT_BASH_REF,
    ACTIVITY_WEIGHT_CHANGE,
    ACTIVITY_WEIGHT_READ,
    BASH_COMMAND_PARAM,
    BASH_TOOL,
    CONVERSATION_HEADING,
    DIGEST_FILE_LIST_MAX_ENTRIES,
    DIGEST_HEAD_BUDGET_DIVISOR,
    DIGEST_HEAD_USER_MESSAGES,
    DIGEST_MAX_CHARS,
    DIGEST_TOOL_PARAM_MAX_CHARS,
    EDIT_TOOLS,
    EMPTY_CONVERSATION_MESSAGE,
    ERROR_LINES_KEPT,
    FILE_LIST_HEADING,
    FILE_PATH_PARAMS,
    LOCAL_COMMAND_MARKERS,
    NONE_PLACEHOLDER,
    OMITTED_TURNS_MARKER_TEMPLATE,
    READ_TOOLS,
    ROLE_HEADINGS,
    SECTION_SEPARATOR,
    TOOL_KEY_PARAMS,
    TRUNCATION_ELLIPSIS,
    WRITE_TOOLS,
)
from session_handoff.project_paths import normalize_path
from session_handoff.redact import neutralize_mentions, redact_secrets
from session_handoff.transcript import Entry, TextBlock, ToolResultBlock, ToolUseBlock

_UNKNOWN_TOOL_NAME = "tool"


class _Slot(IntEnum):
    """Position of each counter in a file's tally; the order is that of `FileActivity`."""

    READS = 0
    EDITS = 1
    WRITES = 2
    BASH_REFS = 3


class EmptyConversationError(Exception):
    """The transcript holds nothing that can be summarised."""


@dataclass(frozen=True)
class FileActivity:
    path: str
    reads: int
    edits: int
    writes: int
    bash_refs: int = 0  # inferred from `Bash` arguments, kept apart from confirmed reads

    @property
    def score(self) -> int:
        """Ranking weight: a change says more than a read, and a read more than a mention."""
        return (
            (self.edits + self.writes) * ACTIVITY_WEIGHT_CHANGE
            + self.reads * ACTIVITY_WEIGHT_READ
            + self.bash_refs * ACTIVITY_WEIGHT_BASH_REF
        )


@dataclass(frozen=True)
class _Chunk:
    """One rendered turn; `opens_task` marks the ones that state the goal (prompts, summary)."""

    text: str
    opens_task: bool


def build_digest(
    entries: Sequence[Entry],
    *,
    max_chars: int = DIGEST_MAX_CHARS,
    error_lines: int = ERROR_LINES_KEPT,
    cwd: Path | None = None,
    activity: Sequence[FileActivity] | None = None,
) -> str:
    """File list followed by the conversation, redacted and within `max_chars`.

    `activity` is the file activity already computed for `entries` and `cwd`; without it the
    digest computes its own. Raises `EmptyConversationError` when no entry renders to anything.
    """
    tool_names = _tool_names_by_id(entries)
    chunks = [
        chunk
        for entry in entries
        if (chunk := _render_entry(entry, tool_names, error_lines)) is not None
    ]
    if not chunks:
        raise EmptyConversationError(EMPTY_CONVERSATION_MESSAGE)
    if activity is None:
        activity = collect_file_activity(entries, cwd=cwd)
    file_list = render_file_list(activity)
    prefix = (
        f"{neutralize_mentions(redact_secrets(file_list))}"
        f"{SECTION_SEPARATOR}{CONVERSATION_HEADING}\n\n"
    )
    body = _cap_chunks(chunks, max_chars - len(prefix))
    return (prefix + body)[:max_chars]


def collect_file_activity(
    entries: Sequence[Entry], *, cwd: Path | None = None
) -> tuple[FileActivity, ...]:
    """Read, edit and write counts per file from the file tools' parameters.

    With `cwd`, `Bash` arguments naming existing files inside it add to a separate `bash_refs`.
    """
    counts: dict[str, list[int]] = {}
    for entry in entries:
        for block in entry.blocks:
            if not isinstance(block, ToolUseBlock):
                continue
            for path, slot in _touches(block, cwd):
                counts.setdefault(path, [0] * len(_Slot))[slot] += 1
    return tuple(FileActivity(path, *tally) for path, tally in counts.items())


def _touches(block: ToolUseBlock, cwd: Path | None) -> list[tuple[str, _Slot]]:
    """(path, counter slot) pairs one tool call adds, all spelled as `normalize_path` does."""
    if block.name == BASH_TOOL:
        command = block.input.get(BASH_COMMAND_PARAM)
        if cwd is None or not isinstance(command, str):
            return []
        return [(path, _Slot.BASH_REFS) for path in files_in_command(command, cwd)]
    slot = _activity_slot(block.name)
    path = _file_path_of(block)
    if slot is None or path is None:
        return []
    return [(normalize_path(path, cwd), slot)]


def render_file_list(activity: Sequence[FileActivity]) -> str:
    if not activity:
        return f"{FILE_LIST_HEADING}\n{NONE_PLACEHOLDER}"
    lines = [render_file_line(item) for item in activity[:DIGEST_FILE_LIST_MAX_ENTRIES]]
    return "\n".join([FILE_LIST_HEADING, *lines])


def render_file_line(item: FileActivity) -> str:
    line = f"- `{item.path}`: {item.reads} reads, {item.edits} edits, {item.writes} writes"
    return f"{line}, {item.bash_refs} bash refs" if item.bash_refs else line


def _activity_slot(tool_name: str) -> _Slot | None:
    if tool_name in READ_TOOLS:
        return _Slot.READS
    if tool_name in EDIT_TOOLS:
        return _Slot.EDITS
    if tool_name in WRITE_TOOLS:
        return _Slot.WRITES
    return None


def _file_path_of(block: ToolUseBlock) -> str | None:
    for param in FILE_PATH_PARAMS:
        value = block.input.get(param)
        if isinstance(value, str) and value:
            return value
    return None


def _tool_names_by_id(entries: Sequence[Entry]) -> dict[str, str]:
    return {
        block.tool_use_id: block.name
        for entry in entries
        for block in entry.blocks
        if isinstance(block, ToolUseBlock)
    }


def _render_entry(entry: Entry, tool_names: dict[str, str], error_lines: int) -> _Chunk | None:
    if _is_bookkeeping(entry):
        return None
    lines: list[str] = []
    for block in entry.blocks:
        if isinstance(block, TextBlock):
            lines.append(block.text)
        elif isinstance(block, ToolUseBlock):
            lines.append(_render_tool_call(block))
        else:
            lines.extend(_render_tool_result(block, tool_names, error_lines))
    if not lines:
        return None
    heading = _heading_for(entry)
    text = neutralize_mentions(redact_secrets("\n".join([heading, *lines])))
    return _Chunk(text, opens_task=heading in (ROLE_HEADINGS["user"], ROLE_HEADINGS["summary"]))


def _is_bookkeeping(entry: Entry) -> bool:
    """Meta entries and local-command records: injected by Claude Code, not the user's work."""
    if entry.is_meta:
        return True
    first = entry.blocks[0] if entry.blocks else None
    return (
        entry.role == "user"
        and isinstance(first, TextBlock)
        and first.text.lstrip().startswith(LOCAL_COMMAND_MARKERS)
    )


def _heading_for(entry: Entry) -> str:
    if entry.is_compact_summary:
        return ROLE_HEADINGS["summary"]
    if entry.blocks and all(isinstance(block, ToolResultBlock) for block in entry.blocks):
        return ROLE_HEADINGS["tool_results"]
    return ROLE_HEADINGS[entry.role]


def _render_tool_call(block: ToolUseBlock) -> str:
    params = [
        f"{key}={_shorten(value)}"
        for key in TOOL_KEY_PARAMS
        if isinstance(value := block.input.get(key), str) and value
    ]
    return f"- {block.name}({', '.join(params)})"


def _render_tool_result(
    block: ToolResultBlock, tool_names: dict[str, str], error_lines: int
) -> list[str]:
    name = tool_names.get(block.tool_use_id, _UNKNOWN_TOOL_NAME)
    if not block.is_error:
        return [f"- {name}: ok"]
    kept = block.text.splitlines()[:error_lines]
    return [f"- {name}: error", *(f"  {line}" for line in kept)]


def _shorten(value: str) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= DIGEST_TOOL_PARAM_MAX_CHARS:
        return collapsed
    return collapsed[:DIGEST_TOOL_PARAM_MAX_CHARS] + TRUNCATION_ELLIPSIS


def _cap_chunks(chunks: Sequence[_Chunk], budget: int) -> str:
    """Join the turns; if they exceed `budget`, keep the opening ones and the latest stretch."""
    separator_cost = len(SECTION_SEPARATOR)
    if sum(len(chunk.text) for chunk in chunks) + separator_cost * len(chunks) <= budget:
        return SECTION_SEPARATOR.join(chunk.text for chunk in chunks)

    marker_reserve = len(OMITTED_TURNS_MARKER_TEMPLATE.format(count=len(chunks)))
    head = _select_head(chunks, budget // DIGEST_HEAD_BUDGET_DIVISOR)
    head_cost = sum(len(text) + separator_cost for text in head.values())
    tail = _select_tail(
        chunks,
        first_index=max(head, default=-1) + 1,
        budget=budget - head_cost - marker_reserve - separator_cost,
    )
    omitted = len(chunks) - len(head) - len(tail)
    parts = [*head.values()]
    if omitted:
        parts.append(OMITTED_TURNS_MARKER_TEMPLATE.format(count=omitted))
    parts.extend(tail)
    return SECTION_SEPARATOR.join(parts)


def _select_head(chunks: Sequence[_Chunk], budget: int) -> dict[int, str]:
    head: dict[int, str] = {}
    spent = 0
    for index, chunk in enumerate(chunks):
        if len(head) >= DIGEST_HEAD_USER_MESSAGES:
            break
        if not chunk.opens_task:
            continue
        cost = len(chunk.text) + len(SECTION_SEPARATOR)
        if spent + cost <= budget:
            head[index] = chunk.text
            spent += cost
        else:
            if not head:
                head[index] = keep_start(chunk.text, budget - len(SECTION_SEPARATOR))
            break
    return head


def _select_tail(chunks: Sequence[_Chunk], *, first_index: int, budget: int) -> list[str]:
    tail: list[str] = []
    remaining = budget
    for chunk in reversed(chunks[first_index:]):
        cost = len(chunk.text) + len(SECTION_SEPARATOR)
        if cost > remaining:
            if not tail:
                tail.append(_keep_end(chunk.text, remaining - len(SECTION_SEPARATOR)))
            break
        tail.append(chunk.text)
        remaining -= cost
    tail.reverse()
    return tail


def keep_start(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters, ellipsis included, when it is longer."""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNCATION_ELLIPSIS))
    return text[:keep] + TRUNCATION_ELLIPSIS


def _keep_end(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNCATION_ELLIPSIS))
    return TRUNCATION_ELLIPSIS + text[len(text) - keep :]
