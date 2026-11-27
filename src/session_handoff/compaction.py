"""What the plugin asks of a compaction and what it keeps from it, as pure text functions."""

import re

from session_handoff.constants import (
    ANALYSIS_BLOCK_PATTERN,
    BRANCH_HEADING,
    HANDOFF_MODEL_SECTIONS,
    HANDOFF_TITLE_PREFIX,
    KEY_FILES_HEADING,
    PRE_COMPACT_FORMAT_INSTRUCTIONS,
    PRE_COMPACT_INTRO,
    PRE_COMPACT_MAX_CHARS,
)
from session_handoff.digest import keep_start

_ANALYSIS_BLOCK = re.compile(ANALYSIS_BLOCK_PATTERN, re.DOTALL)


def pre_compact_instructions() -> str:
    """The text `PreCompact` prints: Claude Code adds it to whatever the user typed after
    `/compact`, so it never carries that text. The same call always gives the same text.
    """
    layout = "\n".join(
        [
            f"{HANDOFF_TITLE_PREFIX}: <topic>",
            *(f"## {name}" for name in HANDOFF_MODEL_SECTIONS),
            KEY_FILES_HEADING,
            BRANCH_HEADING,
        ]
    )
    format_instructions = PRE_COMPACT_FORMAT_INSTRUCTIONS.format(format=layout)
    return keep_start(f"{PRE_COMPACT_INTRO}\n\n{format_instructions}", PRE_COMPACT_MAX_CHARS)


def strip_analysis(compact_summary: str) -> str:
    """The summary without its leading `<analysis>` block; empty when nothing else is left."""
    return _ANALYSIS_BLOCK.sub("", compact_summary, count=1).strip()
