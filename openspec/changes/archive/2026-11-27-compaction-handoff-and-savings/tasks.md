## 1. Spike (throwaway, branch `spike/compact-hooks`; confirms D3–D5 before product code)

- [x] 1.1 Probe plugin `spike/compact-probe/` with `PreCompact`, `PostCompact` and `Stop` hooks that log their stdin JSON to a file; `PreCompact` prints a recognizable instruction ("start the summary with the word PROBE")
- [x] 1.2 [user] With `claude --plugin-dir spike/compact-probe`, run `/compact` and `/compact keep X`; confirm the summary follows the probe instruction, and whether the user's text is still honoured (replace vs extend)
- [x] 1.3 [user] With `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=100000` and a session over 100K, leave it idle ~1 h; confirm `PreCompact`/`PostCompact` fire with `trigger: "auto"`, the instruction is applied, and `Stop` after returning sees the first post-boundary request in the transcript
- [ ] 1.4 Record findings in `design.md` (D3, D5, Open Questions), adjust specs if needed, and remove `spike/` from the working tree

## 2. Transcript boundary metadata (D9)

- [x] 2.1 Extend fixtures: boundaries with real-shaped `compactMetadata` (`auto` and `manual`), one without it, one sidechain; requests before/after with `ephemeral_1h`/`ephemeral_5m` usage
- [x] 2.2 TDD `CompactBoundary` parsing (defensive: missing or wrong-typed fields → `None`) and the file-ordered boundaries + main-thread requests sequence; existing `Transcript` behaviour unchanged

## 3. Measurement rule (D6, `compaction-savings`)

- [x] 3.1 Extract TTL detection from `mode.py` into a shared helper; `mode.py` tests stay green
- [x] 3.2 TDD `savings.measure_boundaries`: expired cache, alive cache (negative `saved`), no `after` → `unused`, unknown TTL and missing metadata → `unknown`, several boundaries in one file, sidechain/synthetic requests ignored; values set by the tests, not by constants

## 4. Ledger (D7)

- [x] 4.1 TDD append (private file, one line per record, `O_APPEND`) and fold by `(session_id, boundary_uuid)` with last-wins and unreadable lines skipped
- [x] 4.2 TDD that `apply_retention` never touches `savings.jsonl` or `pending/`

## 5. Hook plumbing (D1, D2)

- [x] 5.1 `hook_output.py`: event name as a parameter; plain-stdout helper for `PreCompact`
- [x] 5.2 TDD `entry.main` dispatch on `hook_event_name`; unknown events exit 0 silently
- [x] 5.3 TDD `run.py` failure policy: exit 2 only for `UserPromptSubmit` with `handoff`/`savings`; exit 0 for the compaction and `Stop` events on any failure, including old Python; extend `COMMAND_PATTERN` and its sync test
- [x] 5.4 `hooks/hooks.json`: add `PreCompact` (no matcher), `PostCompact`, `Stop` with `timeout: 30`

## 6. Compaction format (`compaction-format`, D3, D4)

- [x] 6.1 TDD `run_pre_compact`: `auto` → format instructions; `manual` with `custom_instructions` → same output (not repeated); output ≤ 10,000 chars with a long custom text; exception → exit 0, empty stdout
- [x] 6.2 TDD `run_post_compact`: strips the `<analysis>` block, writes `.compact.md` (`0600`), applies retention, writes the pending marker; empty summary → no file but marker; invalid session id → no marker; errors logged, exit 0

## 7. Live measurement (D5)

- [x] 7.1 TDD `run_stop`: no marker → no transcript read (injected reader asserts it is not called); marker + `after` present → ledger line and marker removed; marker + still no `after` → marker kept

## 8. Report command (D8)

- [x] 8.1 TDD `command.py` recognizes `/session-handoff:savings` and `/savings` variants; `/savingsx` does not match
- [x] 8.2 TDD back-fill over a fake `projects` dir: top-level files only, files without `compact_boundary` skipped, no duplicates on a second run, `unused` refreshed to `measured`
- [x] 8.3 TDD report rendering: totals, percentage, overhead, per-trigger breakdown, empty ledger message; the whole command blocks and sends nothing to the model
- [x] 8.4 `commands/savings.md` with the same "hook not active" fallback body as `handoff.md`

## 9. Docs, verification and measurement

- [x] 9.1 README: section on compaction format and `/session-handoff:savings` (no published numbers yet); CHANGELOG `[Unreleased]`
- [x] 9.2 `task check` (ruff, mypy, pytest) once, with output pasted (2026-11-26: ruff clean, 72 files formatted, mypy 50 files clean, 464 passed)
- [ ] 9.3 [user] Install the branch, run `/session-handoff:savings` on the real transcripts and decide whether the result is positive enough to publish in the README
