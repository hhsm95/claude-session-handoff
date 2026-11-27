"""Defensive reading of a Claude Code JSONL transcript into typed main-thread entries.

Every field is validated: a line that is not JSON, or a field with the wrong type, is skipped
or counted as absent instead of raising.
"""

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeAlias

from session_handoff.constants import (
    COMPACT_BOUNDARY_SUBTYPE,
    MAIN_THREAD_ROLES,
    SYNTHETIC_MODEL,
)

Role: TypeAlias = Literal["user", "assistant"]
JsonObject: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    tool_use_id: str
    name: str
    input: Mapping[str, object]


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    is_error: bool
    text: str


Block: TypeAlias = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    ephemeral_1h_input_tokens: int = 0
    ephemeral_5m_input_tokens: int = 0


@dataclass(frozen=True)
class Entry:
    role: Role
    blocks: tuple[Block, ...]
    timestamp: datetime | None = None
    message_id: str | None = None
    usage: Usage | None = None
    is_compact_summary: bool = False
    is_meta: bool = False


@dataclass(frozen=True)
class RequestUsage:
    """Usage of one main-thread API request (one `message.id`)."""

    message_id: str | None
    timestamp: datetime | None
    usage: Usage


@dataclass(frozen=True)
class Transcript:
    entries: tuple[Entry, ...]
    """Main-thread entries from the last compaction on, summary first."""
    requests: tuple[RequestUsage, ...]
    """Usage of every main-thread request in the whole file, in order."""


@dataclass(frozen=True)
class CompactBoundary:
    """A main-thread `compact_boundary`; every field but `uuid` is `None` when absent or invalid."""

    uuid: str
    timestamp: datetime | None
    trigger: str | None
    pre_tokens: int | None
    post_tokens: int | None
    session_id: str | None
    cwd: str | None


TimelineItem: TypeAlias = CompactBoundary | RequestUsage


def read_timeline(path: Path) -> tuple[TimelineItem, ...]:
    """Read a transcript file as a timeline; a missing file raises."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        return parse_timeline(handle)


def parse_timeline(lines: Iterable[str]) -> tuple[TimelineItem, ...]:
    """Main-thread boundaries and requests in file order.

    A boundary without a `uuid` has no identity to be measured by and is dropped. Responses are
    merged by `message.id` within the stretch between two boundaries.
    """
    timeline: list[TimelineItem] = []
    stretch: list[Entry] = []

    def close_stretch() -> None:
        timeline.extend(
            RequestUsage(entry.message_id, entry.timestamp, entry.usage)
            for entry in _merge_by_message_id(stretch)
            if entry.usage is not None
        )
        stretch.clear()

    for raw_line in lines:
        record = _decode_object(raw_line)
        if record is None or record.get("isSidechain") is True:
            continue
        if _is_compact_boundary(record):
            boundary = _parse_boundary(record)
            if boundary is not None:
                close_stretch()
                timeline.append(boundary)
            continue
        entry = _parse_entry(record)
        if entry is not None:
            stretch.append(entry)
    close_stretch()
    return tuple(timeline)


def _parse_boundary(record: JsonObject) -> CompactBoundary | None:
    uuid = record.get("uuid")
    if not isinstance(uuid, str) or not uuid:
        return None
    metadata = record.get("compactMetadata")
    fields = metadata if isinstance(metadata, dict) else {}
    trigger = fields.get("trigger")
    session_id = record.get("sessionId")
    cwd = record.get("cwd")
    return CompactBoundary(
        uuid=uuid,
        timestamp=_parse_timestamp(record.get("timestamp")),
        trigger=trigger if isinstance(trigger, str) else None,
        pre_tokens=_optional_count(fields.get("preTokens")),
        post_tokens=_optional_count(fields.get("postTokens")),
        session_id=session_id if isinstance(session_id, str) else None,
        cwd=cwd if isinstance(cwd, str) else None,
    )


def _optional_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def read_transcript(path: Path) -> Transcript:
    """Read a transcript file; unreadable lines are skipped, a missing file raises."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        return parse_transcript(handle)


def parse_transcript(lines: Iterable[str]) -> Transcript:
    raw_entries: list[Entry] = []
    cut_index = 0
    for raw_line in lines:
        record = _decode_object(raw_line)
        if record is None or record.get("isSidechain") is True:
            continue
        if _is_compact_boundary(record):
            cut_index = len(raw_entries)
            continue
        entry = _parse_entry(record)
        if entry is not None:
            raw_entries.append(entry)

    merged = _merge_by_message_id(raw_entries)
    tail = _merge_by_message_id(raw_entries[cut_index:])
    return Transcript(
        entries=_summary_first(tail),
        requests=tuple(
            RequestUsage(entry.message_id, entry.timestamp, entry.usage)
            for entry in merged
            if entry.usage is not None
        ),
    )


def _decode_object(raw_line: str) -> JsonObject | None:
    try:
        decoded = json.loads(raw_line)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _is_compact_boundary(record: JsonObject) -> bool:
    return record.get("type") == "system" and record.get("subtype") == COMPACT_BOUNDARY_SUBTYPE


def _parse_entry(record: JsonObject) -> Entry | None:
    role = record.get("type")
    message = record.get("message")
    if role not in MAIN_THREAD_ROLES or not isinstance(message, dict):
        return None
    if record.get("isApiErrorMessage") is True or message.get("model") == SYNTHETIC_MODEL:
        return None
    message_id = message.get("id")
    return Entry(
        role="user" if role == "user" else "assistant",
        blocks=_parse_blocks(message.get("content")),
        timestamp=_parse_timestamp(record.get("timestamp")),
        message_id=message_id if isinstance(message_id, str) else None,
        usage=_parse_usage(message.get("usage")),
        is_compact_summary=record.get("isCompactSummary") is True,
        is_meta=record.get("isMeta") is True,
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_blocks(content: object) -> tuple[Block, ...]:
    if isinstance(content, str):
        return (TextBlock(content),)
    if not isinstance(content, list):
        return ()
    blocks = (_parse_block(item) for item in content if isinstance(item, dict))
    return tuple(block for block in blocks if block is not None)


def _parse_block(item: JsonObject) -> Block | None:
    kind = item.get("type")
    if kind == "text" and isinstance(item.get("text"), str):
        return TextBlock(str(item["text"]))
    if kind == "tool_use" and isinstance(item.get("name"), str):
        tool_input = item.get("input")
        return ToolUseBlock(
            tool_use_id=_string_or_empty(item.get("id")),
            name=str(item["name"]),
            input=tool_input if isinstance(tool_input, dict) else {},
        )
    if kind == "tool_result":
        return ToolResultBlock(
            tool_use_id=_string_or_empty(item.get("tool_use_id")),
            is_error=item.get("is_error") is True,
            text=_flatten_result_content(item.get("content")),
        )
    return None


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _flatten_result_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = (
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )
    return "\n".join(parts)


def _parse_usage(value: object) -> Usage | None:
    if not isinstance(value, dict):
        return None
    creation = value.get("cache_creation")
    buckets = creation if isinstance(creation, dict) else {}
    return Usage(
        input_tokens=_count(value.get("input_tokens")),
        output_tokens=_count(value.get("output_tokens")),
        cache_read_input_tokens=_count(value.get("cache_read_input_tokens")),
        cache_creation_input_tokens=_count(value.get("cache_creation_input_tokens")),
        ephemeral_1h_input_tokens=_count(buckets.get("ephemeral_1h_input_tokens")),
        ephemeral_5m_input_tokens=_count(buckets.get("ephemeral_5m_input_tokens")),
    )


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def _merge_by_message_id(entries: list[Entry]) -> list[Entry]:
    """Join the entries of one response; blocks stay in order, the last usage wins."""
    merged: list[Entry] = []
    position_by_id: dict[str, int] = {}
    for entry in entries:
        position = position_by_id.get(entry.message_id) if entry.message_id else None
        if position is None:
            if entry.message_id:
                position_by_id[entry.message_id] = len(merged)
            merged.append(entry)
            continue
        earlier = merged[position]
        merged[position] = replace(
            earlier,
            blocks=earlier.blocks + entry.blocks,
            usage=entry.usage or earlier.usage,
        )
    return merged


def _summary_first(entries: list[Entry]) -> tuple[Entry, ...]:
    summaries = [entry for entry in entries if entry.is_compact_summary]
    others = [entry for entry in entries if not entry.is_compact_summary]
    return tuple(summaries + others)
