import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from session_handoff.transcript import (
    CompactBoundary,
    Entry,
    RequestUsage,
    TextBlock,
    TimelineItem,
    ToolResultBlock,
    ToolUseBlock,
    Transcript,
    parse_timeline,
    parse_transcript,
    read_timeline,
    read_transcript,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "session_2_1_330.jsonl"


@pytest.fixture(scope="module")
def transcript() -> Transcript:
    return read_transcript(FIXTURE)


def all_text(entries: tuple[Entry, ...]) -> str:
    return "\n".join(
        block.text for entry in entries for block in entry.blocks if isinstance(block, TextBlock)
    )


def line(**fields: object) -> str:
    return json.dumps(fields)


def user_line(text: str, **extra: object) -> str:
    return line(type="user", message={"role": "user", "content": text}, **extra)


def test_unreadable_lines_and_wrongly_typed_fields_are_skipped(transcript: Transcript) -> None:
    text = all_text(transcript.entries)

    assert "Typo-tolerant text" in text
    assert "stray" not in text
    assert "42" not in text


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN", "Infinity"])
def test_non_finite_usage_numbers_count_as_zero_instead_of_raising(number: str) -> None:
    raw = (
        '{"type": "assistant", "message": {"id": "m", "content": "x", "usage": '
        f'{{"input_tokens": {number}, "output_tokens": 7}}}}}}'
    )

    result = parse_transcript([raw])

    usage = result.requests[0].usage
    assert usage.input_tokens == 0
    assert usage.output_tokens == 7


def test_an_assistant_error_entry_is_dropped_by_its_flag_alone() -> None:
    flagged = line(
        type="assistant",
        isApiErrorMessage=True,
        message={"id": "m1", "model": "claude-opus-4-1", "content": "FLAGGED-ERROR"},
    )

    result = parse_transcript([flagged, user_line("kept")])

    assert all_text(result.entries) == "kept"


def test_the_meta_flag_of_an_entry_is_read() -> None:
    result = parse_transcript([user_line("injected", isMeta=True), user_line("typed")])

    assert [entry.is_meta for entry in result.entries] == [True, False]


def test_non_object_json_lines_are_skipped() -> None:
    result = parse_transcript(["[1, 2]", "42", '"text"', user_line("kept")])

    assert [entry.role for entry in result.entries] == ["user"]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_transcript(tmp_path / "missing.jsonl")


def test_subagent_synthetic_error_and_thinking_content_is_left_out(
    transcript: Transcript,
) -> None:
    text = all_text(transcript.entries)

    for leaked in ("SIDECHAIN-TEXT", "API-ERROR-TEXT", "SYNTHETIC-TEXT", "PRIVATE-THINKING"):
        assert leaked not in text
    tool_names = [
        block.name
        for entry in transcript.entries
        for block in entry.blocks
        if isinstance(block, ToolUseBlock)
    ]
    assert tool_names == ["Read", "Edit", "Bash"]


def test_entries_sharing_a_message_id_merge_into_one_in_order(transcript: Transcript) -> None:
    [merged] = [entry for entry in transcript.entries if entry.message_id == "msg_1"]

    assert [type(block) for block in merged.blocks] == [TextBlock, ToolUseBlock]
    assert merged.blocks[0] == TextBlock("I will look at the file.")


def test_merged_entry_keeps_the_first_timestamp_and_the_last_usage(
    transcript: Transcript,
) -> None:
    [merged] = [entry for entry in transcript.entries if entry.message_id == "msg_1"]

    assert merged.timestamp == datetime(2026, 1, 1, 11, 0, 5, tzinfo=UTC)
    assert merged.usage is not None
    assert merged.usage.output_tokens == 9


def test_transcript_is_cut_at_the_last_compact_boundary_with_its_summary_first(
    transcript: Transcript,
) -> None:
    first = transcript.entries[0]
    text = all_text(transcript.entries)

    assert first.is_compact_summary
    assert first.blocks == (TextBlock("SECOND-SUMMARY text"),)
    for dropped in ("BEFORE-FIRST-BOUNDARY", "FIRST-SUMMARY", "BETWEEN-BOUNDARIES"):
        assert dropped not in text


def test_transcript_without_a_boundary_keeps_every_entry() -> None:
    result = parse_transcript([user_line("one"), user_line("two")])

    assert all_text(result.entries) == "one\ntwo"


def test_summary_is_moved_first_when_an_entry_precedes_it() -> None:
    lines = [
        line(type="system", subtype="compact_boundary"),
        user_line("stray prompt"),
        user_line("the summary", isCompactSummary=True),
    ]

    result = parse_transcript(lines)

    assert all_text(result.entries) == "the summary\nstray prompt"


def test_boundary_inside_a_subagent_thread_does_not_cut() -> None:
    lines = [
        user_line("before"),
        line(type="system", subtype="compact_boundary", isSidechain=True),
        user_line("after"),
    ]

    assert all_text(parse_transcript(lines).entries) == "before\nafter"


def test_tool_result_is_parsed_with_status_and_flattened_text(transcript: Transcript) -> None:
    results = [
        block
        for entry in transcript.entries
        for block in entry.blocks
        if isinstance(block, ToolResultBlock)
    ]
    by_id = {result.tool_use_id: result for result in results}

    assert not by_id["toolu_read"].is_error
    assert by_id["toolu_edit"].text == "edited"
    assert by_id["toolu_bash"].is_error
    assert by_id["toolu_bash"].text.splitlines()[0] == "ERR-1"


def test_request_usage_exposes_both_cache_ttl_buckets_across_the_whole_file(
    transcript: Transcript,
) -> None:
    by_id = {request.message_id: request.usage for request in transcript.requests}

    assert set(by_id) == {"msg_old", "msg_1", "msg_2", "msg_3", None}
    assert by_id["msg_1"].ephemeral_1h_input_tokens == 50
    assert by_id["msg_1"].ephemeral_5m_input_tokens == 0
    assert by_id["msg_3"].ephemeral_5m_input_tokens == 30
    assert by_id["msg_3"].cache_read_input_tokens == 200


def test_wrongly_typed_usage_fields_count_as_zero(transcript: Transcript) -> None:
    [odd] = [
        entry
        for entry in transcript.entries
        if entry.usage is not None and TextBlock("Typo-tolerant text") in entry.blocks
    ]

    assert odd.usage is not None
    assert odd.usage.input_tokens == 0
    assert odd.usage.ephemeral_1h_input_tokens == 0


def test_request_usage_carries_the_timestamp_of_each_request(transcript: Transcript) -> None:
    stamps = {request.message_id: request.timestamp for request in transcript.requests}

    assert stamps["msg_1"] == datetime(2026, 1, 1, 11, 0, 5, tzinfo=UTC)
    assert stamps["msg_old"] == datetime(2026, 1, 1, 10, 0, 5, tzinfo=UTC)


def test_json_booleans_in_usage_are_not_counted_as_numbers() -> None:
    raw = (
        '{"type": "assistant", "message": {"id": "m", "content": "x", "usage": '
        '{"input_tokens": true, "output_tokens": 7, "cache_read_input_tokens": true}}}'
    )

    usage = parse_transcript([raw]).requests[0].usage

    assert usage.input_tokens == 0
    assert usage.cache_read_input_tokens == 0
    assert usage.output_tokens == 7


COMPACTION_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "session_compaction_2_1_332.jsonl"
)


@pytest.fixture(scope="module")
def timeline() -> tuple[TimelineItem, ...]:
    return read_timeline(COMPACTION_FIXTURE)


def boundaries_of(items: tuple[TimelineItem, ...]) -> dict[str, CompactBoundary]:
    return {item.uuid: item for item in items if isinstance(item, CompactBoundary)}


def test_timeline_keeps_file_order_of_boundaries_and_main_thread_requests(
    timeline: tuple[TimelineItem, ...],
) -> None:
    order = [
        item.uuid if isinstance(item, CompactBoundary) else item.message_id for item in timeline
    ]

    assert order == ["msg_a", "cb-auto", "msg_b", "cb-manual", "cb-bare", "msg_c"]


def test_timeline_boundary_exposes_its_compact_metadata(
    timeline: tuple[TimelineItem, ...],
) -> None:
    auto = boundaries_of(timeline)["cb-auto"]

    assert auto == CompactBoundary(
        uuid="cb-auto",
        timestamp=datetime(2026, 2, 1, 10, 5, tzinfo=UTC),
        trigger="auto",
        pre_tokens=238796,
        post_tokens=21000,
        session_id="s-9",
        cwd="/work/app",
    )


def test_timeline_wrong_typed_metadata_fields_become_none(
    timeline: tuple[TimelineItem, ...],
) -> None:
    manual = boundaries_of(timeline)["cb-manual"]

    assert manual.trigger == "manual"
    assert manual.pre_tokens is None
    assert manual.post_tokens is None


def test_timeline_boundary_without_metadata_has_every_field_none(
    timeline: tuple[TimelineItem, ...],
) -> None:
    bare = boundaries_of(timeline)["cb-bare"]

    assert (bare.trigger, bare.pre_tokens, bare.post_tokens) == (None, None, None)
    assert (bare.session_id, bare.cwd) == (None, None)


def test_timeline_ignores_sidechain_boundaries_and_boundaries_without_uuid(
    timeline: tuple[TimelineItem, ...],
) -> None:
    assert set(boundaries_of(timeline)) == {"cb-auto", "cb-manual", "cb-bare"}


def test_timeline_requests_are_main_thread_only_and_merged_by_message_id(
    timeline: tuple[TimelineItem, ...],
) -> None:
    requests = [item for item in timeline if isinstance(item, RequestUsage)]

    assert [request.message_id for request in requests] == ["msg_a", "msg_b", "msg_c"]
    assert requests[2].usage.output_tokens == 7
    assert requests[2].usage.cache_creation_input_tokens == 40


def test_timeline_keeps_the_ttl_buckets_of_each_request(
    timeline: tuple[TimelineItem, ...],
) -> None:
    requests = [item for item in timeline if isinstance(item, RequestUsage)]

    assert requests[0].usage.ephemeral_1h_input_tokens == 221
    assert requests[1].usage.ephemeral_5m_input_tokens == 59734


def test_timeline_of_an_empty_file_is_empty() -> None:
    assert parse_timeline([]) == ()


def test_existing_transcript_view_ignores_the_new_metadata() -> None:
    cut = read_transcript(COMPACTION_FIXTURE)

    assert all_text(cut.entries) == "part one\npart two"
    assert len(cut.requests) == 3
