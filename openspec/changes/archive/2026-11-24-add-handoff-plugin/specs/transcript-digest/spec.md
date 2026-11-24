## ADDED Requirements

### Requirement: Main thread only
The digest SHALL include only `user` and `assistant` entries from the main thread. Entries with
`isSidechain: true`, `isApiErrorMessage: true` or `message.model == "<synthetic>"`, `thinking`
blocks and metadata entries MUST be left out.

#### Scenario: Subagent skipped
- **WHEN** the transcript has `assistant` entries with `isSidechain: true`
- **THEN** their text and `tool_use` blocks do not appear in the digest

#### Scenario: Response split over several entries
- **WHEN** several `assistant` entries share a `message.id`
- **THEN** their blocks appear once, in order

### Requirement: Local bookkeeping left out
The digest SHALL leave out `isMeta` user entries and the records of local commands (user entries
that start with `<local-command-caveat>`, `<command-name>` or `<local-command-stdout>`), both
from the conversation and from the choice of the opening user messages. Bash-mode input and
output (`<bash-input>`, `<bash-stdout>`) are real user work and SHALL be kept.

#### Scenario: Local command
- **WHEN** the transcript has a `/context` record with its stdout and an `isMeta` entry
- **THEN** none of their text appears in the digest, and the opening user messages are the
  first real prompts

#### Scenario: Bash mode
- **WHEN** the user ran `! pytest -q` in bash mode
- **THEN** the digest shows the `<bash-input>` and its `<bash-stdout>`

### Requirement: Empty conversation is an error
The hook SHALL block with an error, and SHALL save no digest, when no entry renders to anything
(an unknown transcript format, or only thinking blocks). This happens before calling Haiku or
choosing warm mode.

#### Scenario: Unknown format
- **WHEN** no line of the transcript is a readable `user` or `assistant` entry
- **THEN** the hook blocks with `Handoff failed` and the model is not called

### Requirement: Tool calls kept, results trimmed
The digest SHALL keep each `tool_use` with its name and key parameters. From each `tool_result`
it SHALL keep only the status and, for an error, its first 15 lines.

#### Scenario: Successful result
- **WHEN** a `Read` returns a 500-line file without error
- **THEN** the digest shows the call with its `file_path` and an ok status, without the content

#### Scenario: Error result
- **WHEN** a `Bash` call returns a 40-line error
- **THEN** the digest includes the first 15 lines of the error

### Requirement: Deterministic file list
The digest SHALL include the list of touched files with read, edit and write counts, computed
only from the parameters of `Read`, `Edit`, `Write` and `NotebookEdit`.

#### Scenario: Counts per file
- **WHEN** `src/a.py` is read twice and edited three times
- **THEN** the list shows `src/a.py` with 2 reads and 3 edits

### Requirement: Key files ranking
The `Key files` section SHALL rank files by a weighted score, not by the raw total: writes and
edits weigh 3, reads 2 and bash refs 1. Ties SHALL keep the order of first appearance. Each line
SHALL still show the raw counts.

#### Scenario: An edit outranks bash mentions
- **WHEN** `a.py` was edited once and `b.sh` was referenced from `Bash` twice
- **THEN** `a.py` is listed before `b.sh`

#### Scenario: A read outranks a single bash mention
- **WHEN** `a.py` was read once and `b.sh` was referenced from `Bash` once
- **THEN** `a.py` is listed before `b.sh`

### Requirement: Files referenced from Bash
When the project directory is known, the file list SHALL also count, as a separate `bash refs`
figure, each `Bash` command argument that names an existing regular file inside the project
directory. Relative arguments resolve against the project directory. Paths outside it, missing
files, directories, flags, digit-only arguments (file descriptors such as the `2` and `1` of
`2>&1`), arguments that cannot be resolved (NUL byte, name too long, symlink loop) and unparsable
commands SHALL NOT count, and one such argument SHALL NOT stop the others from counting. A path
SHALL count at most once per command. The program of each command segment SHALL NOT count: the
first word of the command and the first word after a control operator (`|`, `||`, `&&`, `;`, `&`,
`(` or a newline), skipping `NAME=value` prefixes. The same file named as an argument, as in
`bash tools/run.sh`, does count.

When the project directory is known, the paths of `Read`, `Edit`, `Write` and `NotebookEdit`
calls SHALL be spelled like the `Bash` ones: relative to the project directory when they resolve
inside it, as given otherwise, so one file never appears as two rows.

#### Scenario: Reads done through Bash
- **WHEN** a `Bash` call runs `sed -n '1,40p' src/a.py | head` and `src/a.py` exists in the
  project
- **THEN** the list shows `src/a.py` with 1 bash ref and 0 reads

#### Scenario: Non-file arguments
- **WHEN** a `Bash` call runs `grep -rn "def main" src/missing.py /etc/hosts`
- **THEN** none of its arguments count, because the first file is missing and the second is
  outside the project

#### Scenario: The program being run
- **WHEN** a `Bash` call runs `tools/run.sh src/a.py` and both files exist in the project
- **THEN** `src/a.py` gets 1 bash ref and `tools/run.sh` gets none

#### Scenario: A script passed as an argument
- **WHEN** a `Bash` call runs `bash tools/run.sh`
- **THEN** `tools/run.sh` gets 1 bash ref

#### Scenario: Programs after control operators and environment prefixes
- **WHEN** a `Bash` call runs `FOO=1 cat src/a.py | ./tools/run.sh src/b.py`
- **THEN** `src/a.py` and `src/b.py` get 1 bash ref each and `tools/run.sh` gets none

#### Scenario: Same file named twice in one command
- **WHEN** a `Bash` call runs `diff src/a.py src/a.py`
- **THEN** `src/a.py` gets 1 bash ref

### Requirement: Earlier compactions respected
If the transcript has a `system` entry with `subtype: "compact_boundary"`, the digest SHALL start
at the last one and SHALL open with the compaction summary that follows it.

#### Scenario: Compacted session
- **WHEN** there are two `compact_boundary` entries
- **THEN** nothing before the second one appears, and the summary after it opens the digest

### Requirement: Secret redaction
Before anything is sent or written to disk, the digest SHALL replace secret-looking values with
`[REDACTED]`: `*KEY=`, `*TOKEN=`, `*SECRET=` and `*PASSWORD=` assignments, `Bearer` headers, and
the `sk-`, `ghp_` and `glpat-` prefixes.

#### Scenario: Token in a command
- **WHEN** a `Bash` call contains `export API_TOKEN=abc123`
- **THEN** the digest shows `export API_TOKEN=[REDACTED]`

### Requirement: Mentions neutralized
The digest SHALL carry no live `@path` mention, because `claude -p` expands mentions found in its
stdin, even with `--tools ""`, and the digest holds user prompts verbatim. The system SHALL insert
U+2060 (WORD JOINER) right after every `@` that is immediately followed by a non-whitespace
character and is not preceded by a word character, in the text that is sent and in the text that
is saved. An `@` inside a word (an email address, `git@github.com:x/y.git`) is not a mention and
SHALL be left unchanged.

#### Scenario: Prompt with a file mention
- **WHEN** a user prompt contains `compare with @/etc/hosts`
- **THEN** the digest sent to Haiku and the saved digest contain `@` + U+2060 + `/etc/hosts` and
  no `@/etc/hosts`

#### Scenario: At sign that is not a mention
- **WHEN** the text contains `@` followed by a space or at the end
- **THEN** it is left unchanged

#### Scenario: Email address or git URL
- **WHEN** the text contains `a@b.com` or `git@github.com:x/y.git`
- **THEN** it is left unchanged

#### Scenario: Mention after punctuation
- **WHEN** the text contains `(@src/a.py` or `node_modules/@types/node`
- **THEN** the `@` of each is followed by U+2060

### Requirement: Size cap
If the digest exceeds the configured cap, it SHALL keep the first user messages and the most
recent stretch, and SHALL replace the middle with a marker that gives the number of omitted
turns.

#### Scenario: Oversized digest
- **WHEN** the digest exceeds the cap
- **THEN** the result is within the cap, starts with the first user message, ends with the last
  turn and contains `turns omitted`
