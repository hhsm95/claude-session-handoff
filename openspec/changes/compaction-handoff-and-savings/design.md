## Context

The plugin has one hook today, `UserPromptSubmit`, which returns at once for any prompt other
than `/session-handoff:handoff` and never reads the transcript in that case. Output goes only
through `hook_output.py`, with the event name fixed to `UserPromptSubmit`. `transcript.py` already detects `compact_boundary` to cut
the transcript, but drops its metadata.

Claude Code 2.1.290+ has **idle compaction** (`idleCompaction`, default on): it compacts an
interactive session about an hour after the last request, before the 1-hour cache expires, if the
context is over `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` (200K). The documented hooks around it:

- `PreCompact` (matchers `manual`, `auto`; idle compaction uses `auto`): input has `trigger` and
  `custom_instructions`. It can block with exit 2 or `decision: block`; if it exits 0, its stdout
  (≤ 10,000 chars) is used as the summary instructions. Only `command`/`http`/`mcp_tool` hooks.
- `PostCompact`: input has `trigger` and `compact_summary`; no decision control.
- No hook can run a slash command or skill.

Real data from the author's 406 transcripts (2.1.314–2.1.332): 12 boundaries, all `auto`, all at
54.4–55.4 min idle, `preTokens` 201,907–423,846, `postTokens` 20,634–35,557, `durationMs`
29.9–92.3 s. In 5 of 12 no request followed. A real boundary entry has `uuid`, `sessionId`,
`timestamp`, `logicalParentUuid` and `compactMetadata` (`trigger`, `preTokens`, `postTokens`,
`durationMs`, `cumulativeDroppedTokens`, `preservedSegment`, …); `compactMetadata` is always a
dict. The test fixture's boundaries (`b1`, `b2`) have no `compactMetadata`.

## Goals / Non-Goals

**Goals:**
- Every compaction summary follows the handoff format, and a copy is kept on disk.
- An honest per-compaction token measurement against "resume the same session uncompacted",
  including compactions that were never used and returns that happened with the cache still alive.
- A durable ledger that survives transcript cleanup, back-filled from existing transcripts.
- A one-command report with totals and a percentage, good enough to publish if positive.

**Non-Goals:**
- Money estimates. Only tokens.
- Blocking or replacing Claude Code's compaction (option 1, discarded).
- Measuring `/session-handoff:handoff` + `/clear` savings (that needs the next session linked to
  the old one; possible later change).
- Charts, per-day history, or exporting the ledger. The JSONL is the export.
- Changing idle compaction settings for the user.

## Decisions

### D1. One entry point, dispatch on `hook_event_name`
`hooks.json` adds `PreCompact` (no matcher, so both `auto` and `manual`), `PostCompact` and
`Stop`, all running the same `run.py`. `entry.main` reads `hook_event_name` and routes to
`run_prompt_hook` (today's `run_hook`), `run_pre_compact`, `run_post_compact` or `run_stop`.
*Alternative:* separate scripts per event. Rejected: duplicates the Python-version guard and the
`sys.path` setup, and the dispatch is a tiny table.

### D2. Failure policy per event
`run.py` keeps exit 2 only for `UserPromptSubmit` prompts matching the commands
(`handoff` **or** `savings`; the duplicated `COMMAND_PATTERN` and its sync test are extended).
For `PreCompact`, `PostCompact` and `Stop`, any failure ends in exit 0 with no stdout, logged when
logging is available. A broken `PreCompact` must never block compaction: exit 2 there would skip
an idle compaction or fail a context-limit request.

### D3. PreCompact instructions reuse `format_instructions`
The stdout is plain text (not JSON): a short intro saying this summary will be used to resume the
work and that it must use the following sections **instead of the default numbered sections**,
then `format_instructions` with the six sections. The result is capped at
`PRE_COMPACT_MAX_CHARS = 10_000` with `keep_start`. It does not read the transcript or git: the
hook must be fast and cannot fail on I/O. Deterministic sections (key files, branch) are not
added here; the compactor sees the whole conversation. The hook is idempotent (same input, same
output), because `PreCompact` was seen firing twice for one `/compact`.

*Spike 1.2 (2.1.332, `/compact` and `/compact <text>`):* the stdout reaches the summary (it
started with the probe's marker line and heading), and it is **added** to the user's
`/compact <text>`, which was still honoured. So the user's text is not repeated in our output.
A plain "add a section" instruction kept Claude Code's default 9 numbered sections after ours;
hence the explicit "instead of" wording, still to be confirmed by spike 1.3 (pending).

*Review fix (C1):* the first version reused the Haiku rules of `format_instructions` ("the
digest", "Key files … are added afterwards"), which are false during a compaction and dropped
the file list and branch from every summary. The compaction text is its own constant and asks
for `## Key files` and `## Branch / commits` itself.

### D4. PostCompact copy and pending marker
`compact_summary` arrives raw, with a leading `<analysis>…</analysis>` block that the transcript's
summary entry no longer has (spike 1.2); that block is removed before saving, and a summary that
is empty after removing it counts as empty.
`run_post_compact` writes `compact_summary` with the existing `handoff_paths`/`write_handoff`
under a `.compact.md` suffix, so it matches `HANDOFF_STEM_PATTERN`. Retention is by kind:
`apply_retention` keeps the 10 newest handoff/digest pairs and, separately, the 10 newest
`.compact.md` summaries, so neither kind crowds out the other. Then it creates the marker
`~/.claude/handoffs/pending/<session_id>` (empty, `0600`; the session id is checked against
`^[A-Za-z0-9-]{1,64}$` before use as a filename). The marker is written even if the summary is
empty, because measurement does not depend on the summary.

### D5. Measure on `Stop`, not on `UserPromptSubmit`
When the user returns and submits a prompt, the first request after the boundary does not exist
yet; it exists when the answer finishes. So the live path is `Stop`: if no marker exists for the
session, return immediately (one `stat`, no transcript read). Otherwise read the transcript,
measure every boundary of the file that has an `after`, append them, and delete the marker when
none is left without `after`. Only the last boundary can still get its request, so an `unused`
boundary earlier than the last one is recorded as final.
If `Stop` runs with a marker and the transcript has no boundaries, the marker is deleted on
purpose, so the transcript is not re-read on every `Stop`; the back-fill covers that case.
*Alternative:* measure on the next `UserPromptSubmit`. Rejected: needs a second prompt, and
`Stop` is equally cheap without a marker.

### D6. Measurement rule (`savings.py`, pure)
`measure_boundaries(transcript_entries) -> list[CompactionRecord]`, working on main-thread
requests (sidechain, `<synthetic>` and API errors excluded, as today) in file order:
- `before` = last request before the boundary, `after` = first request after it.
- TTL from `before` using the same rule as `mode.py` (`ephemeral_5m` > 0 → 5 min, `ephemeral_1h`
  > 0 → 60 min); the TTL detection is extracted from `mode.py` into a shared helper so both use
  one rule. Not detectable → status `unknown`.
- Cache would have expired (`gap > ttl`): `counterfactual_uncached = preTokens`. Still alive:
  `counterfactual_uncached = before.input + before.cache_creation`, the typical incremental write
  of a turn. This is an estimate and the report says so.
- `actual_uncached = after.input + after.cache_creation`.
- Overhead: `compaction_cached_read = preTokens`, `compaction_output = postTokens`. Kept apart:
  they are tokens of different kinds (cached read, output) and adding them to uncached input
  would make the percentage meaningless.
- Boundary without `compactMetadata` (old versions, fixtures) → status `unknown`.

Why this baseline: with idle compaction at ~55 min, returning after more than an hour without
compaction re-writes the whole context (`preTokens`). The README already measured that: 23 of 25
pauses over 60 minutes were misses.

### D7. Ledger format
`~/.claude/handoffs/savings.jsonl`, private, append-only, one JSON object per line:
`{"v":1,"session_id","boundary_uuid","project","timestamp","trigger","status","pre_tokens",
"post_tokens","gap_seconds","ttl_seconds","counterfactual_uncached","actual_uncached","saved",
"compaction_cached_read","compaction_output","plugin_version","recorded_at",
"idle_before_seconds"}`; the last one is the time from the last earlier request to the boundary
(`null` when unknown, and for lines written before it existed). The report uses it to split
automatic compactions into `auto idle` (idle at least `IDLE_COMPACTION_MIN_IDLE_SECONDS`) and
`auto limit`.
Reading folds by `(session_id, boundary_uuid)`, last line wins; unreadable lines are skipped.
`project` is the slug of the boundary's `cwd` (or of the transcript's folder name for back-fill).
*Alternative:* rewrite the file deduplicated. Rejected: append-only avoids losing data on a crash
and two hooks writing at once; each line is written with a single `write` call under
`O_APPEND`, which keeps lines whole.

### D8. Back-fill and report
`/session-handoff:savings` is a second command recognized by `command.py`. It walks
`~/.claude/projects/*/*.jsonl` (top-level files only, which are the main sessions; subagent files
live in subfolders), skips files that do not contain the string `compact_boundary` (cheap check
before parsing), measures, and appends records whose key is missing or whose stored status is
`unused`/`unknown` and changed. The candidates of one pass are folded by key first (`measured` >
`unknown` > `unused`) and only then compared with the ledger (`ledger.append_new`, shared with
`Stop`), so a key seen in two files cannot flip between runs. Then it folds the ledger and renders
the report as the block `reason`:

```text
Compaction savings (4 compactions: 3 measured, 1 unused)
  Uncached tokens without compacting: 1,734,000
  Uncached tokens with compacting:      612,000
  Saved: 1,122,000 tokens (64.7%)
  Compaction overhead: 3,120,000 cached read, 310,000 output
  By trigger: auto 1 · auto idle 1 (70.0%) · auto limit 1 (57.5%) · manual 1
Estimate: "without compacting" assumes the cache expired when the gap exceeded its TTL.
```

The base dirs (`~/.claude/projects`, `~/.claude/handoffs`) come from `HookDependencies` so tests
use `tmp_path`.

### D9. Transcript metadata
`transcript.py` adds a `CompactBoundary(uuid, timestamp, trigger, pre_tokens, post_tokens,
duration_ms)` parsed defensively (missing fields → `None`), and a function that returns the
file-ordered sequence of boundaries and main-thread requests needed by D6. The existing
`Transcript` (cut at the last boundary for the digest) does not change.

## Risks / Trade-offs

- [The "cache alive" baseline is an estimate] → it is labelled in the report; the common case
  (return after > 1 h) uses `preTokens`, which is exact.
- [The first request after a boundary may belong to a resumed session written in another file]
  → back-fill is per file; if `after` is in another file the record stays `unused`. The report
  states unused compactions as their own number, so this errs against the feature, not for it.
- [`PreCompact` stdout might not reach idle compaction the same way as `/compact`] → spike task
  1 checks it with `/compact` and with a lowered `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`.
- [`Stop` runs after every answer] → without a marker it is one `stat`; Python start-up time is
  the main cost (~50 ms).
- [Back-fill over hundreds of transcripts is slow] → substring pre-check skips files without
  boundaries; only the report command pays it.
- [Hook timeouts] → `PreCompact` does no I/O; `PostCompact` and `Stop` get `timeout: 30`.

## Migration Plan

Additive. Installing the new version adds the hooks; the ledger is created on first write.
Rollback: uninstall or downgrade; `savings.jsonl`, `pending/` and `.compact.md` files can be
deleted by hand. CHANGELOG under `[Unreleased]`; the README metric is published only if the
measured savings are positive (user's decision after task 9).

## Open Questions

- ~~Does `PreCompact` stdout replace or extend `/compact <text>`?~~ Settled by spike 1.2: it
  extends it (see D3).
- Does idle compaction run `PreCompact`/`PostCompact` with `trigger: "auto"` and apply the
  "instead of the default sections" wording? Documented, not yet observed (task 1.3, pending).

## Review follow-ups (decided by the main session)

- `auto` is split into idle and context-limit compactions in the report by the idle time before
  the boundary (`idle_before_seconds` ≥ 30 min → idle), so context-limit compactions, which have
  no uncompacted alternative, do not drag the percentage.
- Retention is applied per kind: 10 handoff/digest pairs and 10 `.compact.md` files.
- A request after consecutive boundaries belongs only to the last one; the earlier ones are
  `unused`.
- Not done: cleaning up orphan `pending/` markers (tiny files; back-fill covers the
  measurement) and a response split across a boundary (unrealistic, low impact).
