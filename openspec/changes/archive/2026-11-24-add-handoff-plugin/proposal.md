## Why

When a long Claude Code session sits idle for more than an hour, its prompt cache expires. Both
continuing and running `/compact` then re-read the whole conversation without cache, about
$1–2 for a 240K context on Opus 5.5 (measurements in `README.md`). A handoff produced outside the
conversation, by code plus Claude Haiku 5.5, lets the user continue in a clean session for close
to nothing.

## What Changes

- New Claude Code plugin `session-handoff` with the command `/handoff [--haiku] [focus]`.
- A `UserPromptSubmit` hook intercepts `/handoff` and picks a mode from the transcript:
  - **Cold** (cache expired or uncertain): the hook **blocks the prompt**, so the session sends no
    model request. It extracts what matters from the `.jsonl` deterministically and asks Claude
    Haiku 5.5 (`claude -p --model haiku`, on the user's subscription) to write it in a fixed
    format.
  - **Warm** (cache almost certainly alive): the hook lets the prompt through with instructions,
    so the session's own model writes the handoff. It understands the conversation better, and
    reading from cache costs cents.
- The free `focus` text tells the handoff what to prioritize.
- In both modes the user resumes with `/clear` and then pastes an `@<path>` line, which is copied
  to the clipboard when `pbcopy` is available.
- `/clear` is unchanged. The plugin injects nothing into new sessions; using the handoff is the
  user's choice in each session.
- Public GitHub repository following the conventions of `claude-statusbar`: English docs, MIT
  license, changelog, contributing guide, issue and PR templates, cross-platform CI, a
  tag-triggered release and a plugin marketplace manifest.

## Capabilities

### New Capabilities
- `transcript-digest`: deterministic extraction from the transcript. It keeps messages and tool
  calls, trims tool results, drops noise, lists touched files, respects earlier compactions and
  redacts secrets.
- `handoff-mode`: chooses warm or cold from the transcript's cache TTL and idle time. It falls
  back to cold on any doubt and honors `--haiku`.
- `handoff-generation`: the cold path (block, Haiku on the subscription, fixed format, size
  limits, storage and retention) and the instructions given to the session's model in warm mode.
- `handoff-delivery`: how the handoff reaches the user. An `@<path>` line is shown ready to paste
  after `/clear` and copied to the clipboard. There is no state between sessions and no startup
  hook.

### Modified Capabilities
(none; the repository has no specs yet)

## Impact

- New code: plugin manifest, marketplace manifest, `commands/`, `hooks/hooks.json` and a Python
  package that uses only the standard library, plus tests.
- Runtime requirements: the `claude` CLI on `PATH`, a subscription login (`/login`) and
  Python 3.11+.
- Writes only under `~/.claude/handoffs/`. It never modifies transcripts or user settings.
- The scripts in `tools/` stay as measurement tools and are not part of the plugin.
