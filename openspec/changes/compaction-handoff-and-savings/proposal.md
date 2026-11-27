## Why

Claude Code now compacts an idle session about 55 minutes after the last request, before the
1-hour prompt cache expires ("idle compaction"). The author's transcripts show 12 real compactions
since 2.1.314, all `auto` and all at 54.4–55.4 minutes of idle. That compaction already avoids most
of the full re-write that `/session-handoff:handoff` was built to avoid, but its summary does not
follow the handoff format, nothing keeps a copy, and nobody knows whether it actually pays off: in
5 of those 12 sessions no request ever followed the compaction.

We want two things: make every compaction (idle, context limit or `/compact`) produce a summary in
the handoff format, and measure, in tokens, what compacting saves compared with resuming the same
session uncompacted. The measurement decides whether the feature is worth keeping; if it is
positive, it becomes a metric for the README, in the style of RTK's savings report.

## What Changes

- New `PreCompact` hook (matchers `auto` and `manual`) that never blocks and prints the handoff
  format instructions on stdout, which Claude Code uses as the compaction summary instructions.
- New `PostCompact` hook that saves `compact_summary` as a handoff file in the project's handoff
  folder (same storage, permissions and retention) and leaves a pending-measurement marker.
- New `Stop` hook that, only when a marker is pending for the session, measures the compactions of
  that session whose first following request already exists and appends them to a global ledger.
- New command `/session-handoff:savings`, handled by the `UserPromptSubmit` hook and blocked like
  `/session-handoff:handoff`. It first back-fills the ledger from every transcript still under
  `~/.claude/projects` (no duplicates: the key is session id plus boundary uuid), then reports
  totals: compactions (used and unused), tokens avoided, savings percentage, the compaction's own
  overhead, and a breakdown by trigger.
- The transcript reader learns to read `compactMetadata` (`trigger`, `preTokens`, `postTokens`,
  `durationMs`) and the boundary's `uuid`.
- Hook output is no longer tied to `UserPromptSubmit`; the hook entry dispatches on
  `hook_event_name`. A failure in the new hooks never blocks anything (never exit code 2).

## Capabilities

### New Capabilities
- `compaction-format`: the `PreCompact` instructions that shape the compaction summary and the
  `PostCompact` copy of that summary on disk.
- `compaction-savings`: per-compaction token measurement, the durable ledger (live and
  back-filled) and the `/session-handoff:savings` report.

### Modified Capabilities
<!-- None: the existing requirements of handoff-mode, handoff-generation, handoff-delivery and
     transcript-digest do not change. The extra compactMetadata parsing is additive and only feeds
     compaction-savings. -->

## Impact

- `hooks/hooks.json`: three new events (`PreCompact`, `PostCompact`, `Stop`).
- `hooks/run.py`: the exit-2 fallback also covers `/session-handoff:savings`, and never applies to
  the new events.
- `src/session_handoff/`: `entry.py` (dispatch), `hook_output.py` (event name as a parameter),
  `transcript.py` (boundary metadata), `constants.py`, plus new modules for compaction instructions,
  savings measurement, ledger and report.
- `commands/savings.md`: new command file.
- New files on disk: `~/.claude/handoffs/savings.jsonl` (ledger) and per-session marker files.
- README and CHANGELOG. The README metric is only published if the measured savings are positive.
- No new runtime dependencies; still Python ≥ 3.11 standard library only.
