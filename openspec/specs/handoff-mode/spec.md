# handoff-mode Specification

## Purpose
Choose between warm mode (the session's own model writes the handoff from its cached context) and cold mode (Haiku writes it while the prompt is blocked) from the transcript's cache TTL and the time since the last request, falling back to cold on any doubt.
## Requirements
### Requirement: Conservative mode decision
The system SHALL choose warm mode only when the TTL of the last main-thread request that wrote to
the cache is known, and the idle time since the last main-thread request (the last entry that
carried `usage`) is under that TTL's threshold: 50 min for `1h` and 4 min for `5m`. The TTL is
`1h` only when that request wrote no `ephemeral_5m_input_tokens`, because the 5-minute part is the
conversation tail and expires first. Entries that sent no request (bash-mode input and output,
local commands such as `/context`, `isMeta` entries, a blocked prompt) SHALL NOT refresh the idle
time. In every other case the system SHALL choose cold mode.

#### Scenario: Recent 1-hour cache
- **WHEN** the last request wrote with `ephemeral_1h_input_tokens > 0` and the idle time is 30 min
- **THEN** the mode is warm

#### Scenario: Expired 1-hour cache
- **WHEN** the TTL is `1h` and the idle time is 50 min or more
- **THEN** the mode is cold

#### Scenario: Usage overage (5-minute TTL)
- **WHEN** the last request wrote only with `ephemeral_5m_input_tokens > 0` and the idle time is
  6 min
- **THEN** the mode is cold

#### Scenario: Request wrote both TTLs
- **WHEN** the last request that wrote to the cache has both `ephemeral_1h_input_tokens > 0` and
  `ephemeral_5m_input_tokens > 0` and the idle time is 6 min
- **THEN** the mode is cold

#### Scenario: Local entries after an old request
- **WHEN** the last request was 2 h ago and later entries are bash-mode input, a local command
  such as `/context`, or an `isMeta` entry
- **THEN** the idle time is still 2 h and the mode is cold

#### Scenario: Unknown TTL
- **WHEN** no main-thread request has a readable `cache_creation`
- **THEN** the mode is cold

#### Scenario: Error while deciding
- **WHEN** reading the transcript raises an exception
- **THEN** the mode is cold

### Requirement: Forcing Haiku
If the `/handoff` arguments include `--haiku`, the system SHALL use cold mode whatever the idle
time. The flag SHALL be removed from the focus text.

#### Scenario: Live cache with --haiku
- **WHEN** the user types `/handoff --haiku continue with the parser` and the cache is alive
- **THEN** the mode is cold and the focus is `continue with the parser`

### Requirement: Warm mode without blocking
In warm mode the hook MUST NOT block. It SHALL save the digest and SHALL return
`additionalContext` with a `<session-handoff>` block. The block SHALL hold, in this order, the
target path, the closing instruction (write the file, then answer with
`Handoff ready. To resume: /clear, then paste this line:` and the `@<path>` alone on the next
line, without backticks), the fixed format, the size limit, the focus and the deterministic
sections. The whole `additionalContext`, wrapped as it is emitted, SHALL stay under 9,500
characters for any focus, key files and git text: the focus is capped at 1,000 characters, key
files are trimmed first and the git text is shortened last. The instruction that introduces the
deterministic sections SHALL state that the two marker lines around them are delimiters, and that
the model writes only the text between them, verbatim, without the two marker lines.

#### Scenario: Marker lines are delimiters
- **WHEN** the warm block is built
- **THEN** the instruction before the deterministic sections says to copy only the text between
  the marker lines, without the two marker lines

#### Scenario: /handoff with a live cache
- **WHEN** the mode is warm
- **THEN** the hook output has no `decision: "block"`, the `<session-handoff>` block contains the
  absolute handoff path, and the digest already exists on disk

#### Scenario: Many long key file paths
- **WHEN** the session touched dozens of files with long paths
- **THEN** the block lists fewer key files so that `additionalContext` stays under 9,500
  characters, and the closing instruction is still present

#### Scenario: Very long focus
- **WHEN** the user passes a focus of thousands of words
- **THEN** the block carries only its first 1,000 characters, `additionalContext` stays under
  9,500 characters and the closing instruction is still present

