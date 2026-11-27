## ADDED Requirements

### Requirement: Per-compaction token measurement
For each main-thread `compact_boundary` the plugin SHALL compute, from the transcript only:

- `before`: the last main-thread request before the boundary; `after`: the first one after it.
  A request is the `after` of the last boundary that precedes it only: an earlier boundary with
  no request of its own before the next boundary has no `after`.
- `idle_before`: time between `before` and the boundary, or none when either has no timestamp.
- `gap`: time between `before` and `after`; `ttl`: the cache TTL detected as in handoff mode.
- `actual_uncached` = `after.input_tokens + after.cache_creation_input_tokens`.
- `counterfactual_uncached` = `preTokens` when `gap` > `ttl` (the cache would have expired), or
  `before.cache_creation_input_tokens + before.input_tokens` when the cache would still be alive.
- `saved` = `counterfactual_uncached − actual_uncached` (may be negative).
- Overhead, reported apart and never subtracted: `compaction_cached_read` = `preTokens` and
  `compaction_output` = `postTokens`.

A boundary without `after` SHALL be recorded as `unused` with `saved` = 0 and its overhead.
A boundary whose TTL cannot be detected, or whose `gap` is negative (a clock skew), SHALL be
recorded as `unknown` and excluded from the savings totals.

#### Scenario: Cache would have expired
- **WHEN** a boundary has `preTokens` 238796, `gap` 70 minutes, a 1-hour TTL, and `after` wrote 59734 and had 135 fresh input tokens
- **THEN** `counterfactual_uncached` is 238796, `actual_uncached` is 59869 and `saved` is 178927

#### Scenario: Cache still alive on return
- **WHEN** `gap` is 58 minutes with a 1-hour TTL and `before` wrote 221 and had 3 fresh input tokens
- **THEN** `counterfactual_uncached` is 224 and `saved` is `224 − actual_uncached`, a negative number

#### Scenario: Nothing after the compaction
- **WHEN** a boundary has no main-thread request after it
- **THEN** the record has status `unused`, `saved` 0 and the overhead fields filled

#### Scenario: Two compactions in a row
- **WHEN** two boundaries follow each other with no request between them and a request comes after the second
- **THEN** the first is `unused` with its overhead and the second is `measured`

#### Scenario: Clock skew
- **WHEN** the `after` request is stamped earlier than `before`
- **THEN** the record is `unknown`

### Requirement: Durable ledger without duplicates
Measurements SHALL be appended as JSON lines to `~/.claude/handoffs/savings.jsonl` (private file)
with at least: session id, boundary uuid, project slug, boundary timestamp, trigger, status
(`measured`, `unused`, `unknown`), the token fields above and the plugin version. The key is
`(session_id, boundary_uuid)`; when a key appears more than once the last line wins, so an
`unused` record can later become `measured`. The record also carries `idle_before` in seconds
(optional: older lines without it read as none). The ledger SHALL NOT be touched by handoff
retention. An append SHALL first add a newline when the file does not end in one, so a line cut
short by a crash cannot swallow the next record. Within one back-fill pass a key found in several
files keeps one record, preferring `measured` over `unknown` over `unused`. A record that differs
from the stored one only in `recorded_at` or `plugin_version` is not appended again.

#### Scenario: Same boundary measured twice
- **WHEN** the live path and the back-fill both record boundary `b1` of session `s1`
- **THEN** the report counts `b1` once, using the last line

#### Scenario: Unused becomes measured
- **WHEN** `b1` was recorded `unused` and the session is resumed and gets a request after the boundary
- **THEN** a new `measured` line for `b1` is appended and the report uses it

### Requirement: Live measurement after compaction
`PostCompact` SHALL leave a per-session pending marker. On `Stop`, when and only when a marker
exists for the session, the plugin SHALL measure that session's boundaries that have an `after`
request, append them, and remove the marker once the last boundary of the session has its `after`
(an earlier boundary without one is final and is appended as `unused`). Without a marker, `Stop` SHALL exit without reading the transcript. `Stop` SHALL never
block and never fail visibly.

#### Scenario: First answer after an idle compaction
- **WHEN** the user returns, sends a prompt, the model answers and `Stop` runs with a pending marker
- **THEN** the boundary is appended as `measured` and the marker is removed

#### Scenario: Ordinary turn
- **WHEN** `Stop` runs and no marker exists for the session
- **THEN** the hook exits 0 without reading the transcript or writing anything

### Requirement: Savings report command
`/session-handoff:savings` SHALL be handled by the `UserPromptSubmit` hook and blocked so the
session's model receives nothing. Before reporting it SHALL back-fill the ledger from every
`*.jsonl` under `~/.claude/projects` (main session files only), adding the boundaries not yet in
the ledger and refreshing `unused` ones. The report SHALL show, over records with status
`measured` or `unused`: number of compactions (measured and unused), total `counterfactual_uncached`,
total `actual_uncached`, total `saved`, savings percentage (`saved / counterfactual_uncached`),
total overhead (cached read and output), and the same totals per trigger. The `auto` trigger is
split into `auto idle` (`idle_before` of at least 30 minutes) and `auto limit` (less than that);
an `auto` record without `idle_before` stays `auto` and `manual` is not split. `unknown` records
are only counted.

#### Scenario: Report
- **WHEN** the ledger holds 7 measured and 5 unused compactions
- **THEN** the block message shows 12 compactions (7 measured, 5 unused), the token totals, the savings percentage and the per-trigger breakdown

#### Scenario: Empty ledger
- **WHEN** no transcript has any compaction
- **THEN** the block message says no compactions were found and nothing reaches the model

#### Scenario: Hook cannot run
- **WHEN** the hook fails before handling `/session-handoff:savings` (for example, old Python)
- **THEN** it exits with code 2 so the prompt is blocked, as with `/session-handoff:handoff`
