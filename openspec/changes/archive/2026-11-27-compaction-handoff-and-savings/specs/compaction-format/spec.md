## ADDED Requirements

### Requirement: Compaction summary follows the handoff format
On `PreCompact` (triggers `auto` and `manual`) the plugin SHALL print on stdout instructions that
ask the compaction summary to use the handoff layout: a `# Handoff: <topic>` title and the
sections Goal, Current state, Decisions and why, Ruled out and why, Next steps, How to verify, plus
`## Key files` (paths read or edited and what each is for) and `## Branch / commits` (branch,
relevant commits and working tree state, when known). The instructions SHALL ask to keep the exact
commands and errors needed to continue and to write the body in the conversation's language. They
SHALL NOT refer to a digest or say that sections are added afterwards, because a compaction writes
every section itself. The output SHALL NOT exceed 10,000 characters.

#### Scenario: Automatic compaction
- **WHEN** Claude Code runs `PreCompact` with `trigger: "auto"`
- **THEN** the hook exits 0 and stdout holds the handoff format instructions, at most 10,000 characters

#### Scenario: Manual compaction with instructions
- **WHEN** the user runs `/compact keep the parser details` and `custom_instructions` is `"keep the parser details"`
- **THEN** stdout holds the same handoff format instructions, without repeating the user's text (Claude Code adds both)

#### Scenario: Default sections replaced
- **WHEN** the hook builds its output
- **THEN** the instructions say to use the handoff sections instead of the default numbered sections

### Requirement: The PreCompact hook never blocks compaction
The `PreCompact` hook SHALL always exit 0 and SHALL NOT emit `decision: "block"`, whatever happens,
including malformed input, an unreadable transcript, an exception or an unsupported Python version.

#### Scenario: Internal error
- **WHEN** the `PreCompact` hook raises an exception while building its output
- **THEN** it exits 0 with empty stdout, logs the error, and the compaction proceeds with Claude Code's default instructions

#### Scenario: Old Python
- **WHEN** `python3` is older than 3.11 and Claude Code runs `PreCompact`
- **THEN** the hook exits 0 without output

### Requirement: The compaction summary is kept as a handoff file
On `PostCompact` the plugin SHALL write `compact_summary` to the project's handoff folder as a
private file (`0600`, folder `0700`) named like the other handoffs with a `.compact.md` suffix,
and SHALL apply the same per-project retention, counting handoff/digest pairs and `.compact.md`
files apart (the newest 10 of each). The summary SHALL pass through secret redaction before it is
written. The hook SHALL never fail visibly: errors are logged and the hook exits 0, and the
pending-measurement marker is left even when the summary cannot be saved (for instance without
`cwd`).

#### Scenario: Summary saved
- **WHEN** `PostCompact` arrives with a non-empty `compact_summary` for a project
- **THEN** a `<timestamp>-<session>.compact.md` file with that summary exists in `~/.claude/handoffs/<project-slug>/`

#### Scenario: Analysis block removed
- **WHEN** `compact_summary` starts with an `<analysis>…</analysis>` block followed by the summary
- **THEN** the saved file holds only the text after that block

#### Scenario: Empty summary
- **WHEN** `PostCompact` arrives with an empty or missing `compact_summary`
- **THEN** no handoff file is written and the hook still exits 0

#### Scenario: Secrets redacted
- **WHEN** `compact_summary` contains `export API_TOKEN=supersecret123`
- **THEN** the saved file holds `API_TOKEN=[REDACTED]` and not the value

#### Scenario: No cwd
- **WHEN** `PostCompact` arrives with a `session_id` but without `cwd`
- **THEN** no handoff file is written, the pending marker is still created and the hook exits 0

#### Scenario: Summaries do not evict handoffs
- **WHEN** a project folder holds 10 handoff/digest pairs and 10 `.compact.md` files and another summary is saved
- **THEN** the 10 newest handoff pairs and the 10 newest summaries remain
