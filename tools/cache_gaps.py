"""List main-chain requests of a transcript with the gap since the previous one and cache hit info.

Usage: python3 -I cache_gaps.py <transcript.jsonl> [min_gap_minutes]
"""
import json
import sys
from datetime import datetime


def ts(entry):
    value = entry.get("timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None


def main():
    path = sys.argv[1]
    min_gap = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    entries = []
    with open(path, encoding="utf8") as handle:
        for line in handle:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue

    seen_ids = set()
    prev_end = None
    last_ts = None
    between_types = []
    for entry in entries:
        stamp = ts(entry)
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        is_request = (
            entry.get("type") == "assistant"
            and not entry.get("isSidechain")
            and not entry.get("isApiErrorMessage")
            and message.get("model") != "<synthetic>"
            and isinstance(message.get("usage"), dict)
        )
        if not is_request:
            between_types.append(entry.get("type"))
            if stamp:
                last_ts = stamp
            continue
        mid = message.get("id")
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        usage = message["usage"]
        creation = usage.get("cache_creation") or {}
        read = usage.get("cache_read_input_tokens") or 0
        written = usage.get("cache_creation_input_tokens") or 0
        start = last_ts or stamp
        gap = (start - prev_end).total_seconds() / 60 if prev_end and start else None
        if gap is None or gap >= min_gap:
            kinds = sorted({k for k in between_types if k not in ("user", "assistant")})
            print(
                f"{start:%m-%d %H:%M}Z gap={'-' if gap is None else f'{gap:6.1f}m'} "
                f"read={read:>7} write={written:>7} "
                f"(1h={creation.get('ephemeral_1h_input_tokens', 0)} 5m={creation.get('ephemeral_5m_input_tokens', 0)}) "
                f"version={entry.get('version')} between={kinds}"
            )
        prev_end = stamp
        between_types = []


if __name__ == "__main__":
    main()
