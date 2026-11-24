# claude-session-handoff

A Claude Code plugin that turns a long session into a short handoff document you can resume in a
fresh session, without paying to re-read the whole conversation once its prompt cache has expired.

```text
> /session-handoff:handoff keep going with the parser bug; CI is fine now
  UserPromptSubmit operation blocked by hook:
  Handoff ready with Haiku (18,402 chars, ~4,600 tokens; idle 72 min).
  To resume: /clear, then paste this line (already in your clipboard):
  @/Users/you/.claude/handoffs/-Users-you-code-app/20261123-141205-ab12cd34.md
```

Type `/hand` and accept the autocomplete: plugin commands are namespaced, so the full name is
`/session-handoff:handoff`.

## Why

Claude Code caches the prompt prefix of a session. The cache is keyed by the exact content of
that prefix, and it expires one TTL after the last request that read it: 1 hour on a
subscription, or 5 minutes in usage overage. An active session never loses it. An idle one does.

When you come back to an expired session, the next turn re-writes the whole conversation at full
price. `/compact` does not avoid that: compacting is itself a request that reads the whole
conversation, and the compacted prompt is new content that the next turn writes again.

Measured on the author's own transcripts (aggregate numbers only):

- Resuming within the TTL hits the cache: one resume read 252,487 cached tokens and wrote 820.
- Pauses of 40–55 minutes: 12 of 12 hits. Pauses over 60 minutes: 23 of 25 misses, each
  re-writing the whole conversation (for example 222,309 tokens after 63.6 minutes).
- With Opus 5.5 prices (input $4/MTok, 1-hour cache write $8/MTok, cache read $0.20/MTok), a miss
  costs about 40× more per token than a hit.

For a 240K-token context:

| Option | Cost when you come back | What you lose |
|---|---|---|
| Keep going (cache expired) | re-write 240K ≈ $1.92 | nothing |
| `/compact` after it expired | read 240K uncached + write ~100K ≈ $1.80–2.70 | old detail |
| `/compact` before it expires | read 240K cached ≈ $0.05 + write ~100K later ≈ $0.80 | old detail |
| **`/session-handoff:handoff`, then `/clear`** | cents (see [Modes](#modes)) | what the handoff misses |

## How it works

`/session-handoff:handoff` is handled by a `UserPromptSubmit` hook before the prompt reaches the
model. The hook reads the session transcript and picks one of two modes.

### Modes

| Mode | When | Who writes the handoff | Cost |
|---|---|---|---|
| **Cold** | The cache has expired, or the hook cannot be sure it is alive | Claude Haiku, through `claude -p` on your subscription. The hook **blocks** the prompt, so the session's model receives nothing | A fraction of a cent of Haiku |
| **Warm** | Less than 50 minutes since the last request (4 minutes with a 5-minute TTL) | The session's own model, which saw everything. The prompt goes through with instructions, and the context is read from cache | Cents: a cached read plus about 6K output tokens |

- Any doubt (unknown TTL, unreadable transcript, an error) means cold. There is deliberately no
  way to force warm, because forcing it with an expired cache would pay the full re-read.
- `--haiku` forces cold when you want to save quota on the large model.
- If the hook itself cannot run (for example, `python3` older than 3.11), it still blocks
  `/session-handoff:handoff` with exit code 2, and every other prompt is left alone.

### The handoff

Cold mode first builds a deterministic **digest** of the transcript with plain code:

- your messages, the model's answers, and every tool call with its key parameters;
- for tool results, only the status, plus the first 15 lines of errors;
- a file list with read/edit/write counts. Files named in `Bash` commands (`sed -n`, `cat`,
  `rtk read`, `pytest tests/x.py`, …) count as "bash refs" when they exist inside the project;
- branch, last commits and `git status`;
- no thinking blocks, no subagent traffic, no local commands. If the session was compacted
  before, it starts from the last compaction summary.

Haiku turns the digest into a fixed format: Goal, Current state, Decisions and why, Ruled out and
why, Next steps, How to verify. Code then appends Key files and Branch / commits, plus a pointer
to the full digest. Warm mode gives the session's model the same format and the same
code-generated sections.

The handoff is capped at 32,000 characters (about 8K tokens).

## Requirements

- Claude Code, logged in with a subscription (`/login`). Cold mode calls `claude -p --model haiku`
  and removes `ANTHROPIC_API_KEY` and other provider routing from that call's environment, so it
  uses the subscription.
- `python3` 3.11 or newer on your `PATH`. Only the standard library is used.
- `git` on your `PATH` (optional; without it the Branch / commits section says so).
- macOS or Linux. `pbcopy` (macOS) is used for the clipboard when it exists. Windows is untested:
  the hook calls `python3`, which many Windows setups do not provide.

## Install

In Claude Code:

```text
/plugin marketplace add hhsm95/claude-session-handoff
/plugin install session-handoff@session-handoff
```

To try a local checkout without installing it:

```bash
claude --plugin-dir /path/to/claude-session-handoff
```

## Usage

```text
/session-handoff:handoff [--haiku] [focus]
```

1. In the session you want to leave, type `/hand`, accept the autocomplete, and optionally add a
   focus: what the next session should prioritize, or what no longer matters.
2. Run `/clear`.
3. Paste the `@<path>` line. The `@` mention attaches the file to that same request, without a
   `Read` tool call.

`/clear` stays a plain `/clear`: the plugin injects nothing into new sessions, so using a handoff
is always your choice.

## Files and privacy

- Handoffs and digests are saved under `~/.claude/handoffs/<project>/` as
  `<UTC timestamp>-<session>.md` and `.digest.md`. The last 10 pairs per project are kept.
- Diagnostics go to `~/.claude/handoffs/handoff.log`.
- Folders are created `0700` and files `0600`. In warm mode, Claude Code's `Write` tool creates the
  handoff with your umask; the `0700` folder still keeps other users out.
- Before anything is saved or sent, values that look like secrets (`*_KEY=`, `*_TOKEN=`,
  `Bearer …`, `sk-…`, `ghp_…`, `glpat-…`) are replaced with `[REDACTED]`. This is pattern-based
  and will not catch everything.
- `@path` mentions in the digest are neutralized with an invisible U+2060 after the `@`. Without
  it, `claude -p` would attach those files to the Haiku call.
- Nothing leaves your machine except the cold-mode Haiku call, which goes to Anthropic through
  your own Claude Code login.

## Measurement tools

`tools/` holds the two scripts behind the numbers above. Run them with `python3 -I`, because they
read transcripts:

- `cache_gaps.py <transcript.jsonl> [min_gap_min]` lists main-thread requests with the pause
  before each one and how many tokens they read from or wrote to the cache.
- `compact_cost.py <transcript.jsonl>...` shows, for each compaction, its trigger, the context
  size before it, the idle time before it and the cache use around it.

## Development

Requires [uv](https://docs.astral.sh/uv/) and, optionally, [Task](https://taskfile.dev).

```bash
uv sync
task check       # ruff + mypy (strict) + pytest, what CI runs
task dev         # claude --plugin-dir .
```

The design, specs and task list live in `openspec/`. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
