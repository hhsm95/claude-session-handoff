# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-27

### Added

- Every compaction (idle, context limit or `/compact`) is now asked to write its summary in the
  handoff format, and a copy is saved next to the handoffs as `.compact.md`.
- `/session-handoff:savings` command: back-fills a ledger (`~/.claude/handoffs/savings.jsonl`)
  from your transcripts and reports the uncached tokens each compaction saved, the compaction
  overhead apart, and the compactions not yet used counted separately. Automatic compactions
  are split into `auto idle` and `auto limit` by how long the session sat idle before them.
- The compaction summary copy is redacted for secrets before it is saved, and retention keeps
  the 10 newest summaries apart from the 10 newest handoff pairs.

## [0.1.0] - 2026-11-24

### Added

- `/session-handoff:handoff [--haiku] [focus]` command, handled by a `UserPromptSubmit` hook,
  that turns the current session into a handoff document. You resume after `/clear` by pasting
  an `@<path>` line, which is also copied to the clipboard when `pbcopy` exists.
- Cold mode: when the prompt cache has expired or cannot be trusted, the hook blocks the prompt,
  so the session's model receives nothing, and Claude Haiku writes the handoff through
  `claude -p` on the subscription.
- Warm mode: when the cache is almost certainly alive (under 50 minutes since the last request,
  or 4 minutes with a 5-minute TTL), the session's own model writes the handoff from its cached
  context.
- Deterministic transcript digest: main thread only, tool results trimmed, local commands and
  meta entries dropped, earlier compactions respected, secrets redacted and `@` mentions
  neutralized. Its file list counts files read and edited through tools or named in `Bash`
  commands.
- Fixed handoff format with code-generated Key files and Branch / commits sections, capped at
  32,000 characters.
- Storage under `~/.claude/handoffs/<project>/` with owner-only permissions, keeping the last 10
  handoffs per project.
- Fail-safe behavior: any error, including a `python3` older than 3.11, still blocks the command
  instead of letting it reach the model.
- CI on Linux, macOS and Windows with Python 3.11 and 3.13, a tag-triggered GitHub Release, and a
  single-plugin marketplace manifest.
