# handoff-delivery Specification

## Purpose
Hand the finished handoff to the user as one `@<path>` line to paste after `/clear`, copied to the clipboard when possible, without changing how new sessions start.
## Requirements
### Requirement: Resume line ready to paste
In both modes, the system SHALL give the user a line of its own with the exact shape
`@<absolute handoff path>`. In cold mode it is in the block `reason`. In warm mode it is in the
model's answer, as instructed by the `<session-handoff>` block. The cold-mode message SHALL also
include the size in characters, the approximate size in tokens, the idle time in minutes and the
instruction to run `/clear`.

#### Scenario: Cold handoff ready
- **WHEN** `/handoff` succeeds in cold mode
- **THEN** the message has a line that is exactly `@/…/<timestamp>-<session8>.md`, and that path
  exists

### Requirement: Copy to the clipboard
If `pbcopy` is on `PATH`, the system SHALL copy the `@<path>` line and the message SHALL say it is
already in the clipboard. If `pbcopy` is missing or fails, the system SHALL carry on without
error and drop that phrase.

#### Scenario: No pbcopy
- **WHEN** `pbcopy` is not on `PATH`
- **THEN** the handoff is still produced and the message does not mention the clipboard

### Requirement: No effect on new sessions
The plugin MUST NOT register `SessionStart` hooks or keep state that changes a new session.
`/clear` SHALL behave the same with the plugin installed as without it.

#### Scenario: /clear without using the handoff
- **WHEN** the user runs `/clear` after `/handoff` and types another prompt
- **THEN** the new session receives no handoff content

