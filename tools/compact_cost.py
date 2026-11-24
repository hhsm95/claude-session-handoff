"""For each compact_boundary in a transcript: trigger, preTokens, idle gap before it, and the
cache usage of the last main-chain request before and the first one after.

Usage: python3 -I compact_cost.py <transcript.jsonl>...
"""
import json
import sys
from datetime import datetime


def ts(entry):
    value = entry.get("timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None


def usage_of(entry):
    message = entry.get("message")
    if entry.get("type") != "assistant" or entry.get("isSidechain") or not isinstance(message, dict):
        return None
    if entry.get("isApiErrorMessage") or message.get("model") == "<synthetic>":
        return None
    return message.get("usage") if isinstance(message.get("usage"), dict) else None


def fmt(usage):
    if usage is None:
        return "none"
    read = usage.get("cache_read_input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    fresh = usage.get("input_tokens") or 0
    return f"prompt={read + write + fresh:>7} read={read:>7} write={write:>7}"


def scan(path):
    entries = []
    with open(path, encoding="utf8") as handle:
        for line in handle:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    for index, entry in enumerate(entries):
        if entry.get("subtype") != "compact_boundary":
            continue
        meta = entry.get("compactMetadata") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        before = next(
            (e for e in reversed(entries[:index]) if usage_of(e) is not None), None
        )
        after = next((e for e in entries[index + 1 :] if usage_of(e) is not None), None)
        gap = None
        if before is not None and ts(before) and ts(entry):
            gap = (ts(entry) - ts(before)).total_seconds() / 60
        print(
            f"{meta.get('trigger', '?'):>6} pre={meta.get('preTokens', '?'):>7} "
            f"idle_before={'?' if gap is None else f'{gap:5.1f}m'} "
            f"dur={(meta.get('durationMs') or 0) / 1000:5.1f}s | "
            f"before: {fmt(usage_of(before) if before else None)} | "
            f"after: {fmt(usage_of(after) if after else None)}"
        )


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        scan(arg)
