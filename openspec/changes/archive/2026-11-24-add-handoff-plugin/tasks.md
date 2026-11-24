## 1. Spike (throwaway, branch `spike/hook-probe`; confirms D2 before any product code)

- [x] 1.1 Check `claude --help`: whether `--no-session-persistence` exists, how to disable tools (`--tools ""` / `--allowedTools`) and `--system-prompt`; record the chosen flags in `design.md` (D5)
- [x] 1.2 From a subprocess with `CLAUDECODE=1`, without `ANTHROPIC_API_KEY` and in a temporary `cwd`, run `claude -p --model haiku` with a short prompt on stdin; confirm it answers, that it uses the subscription, and whether a session shows up under `~/.claude/projects/`
- [x] 1.3 Build a minimal probe plugin (`spike/probe-plugin/`: `plugin.json`, `commands/handoff.md`, `hooks/hooks.json` with `UserPromptSubmit` and `timeout: 300`, a script that logs its stdin JSON and answers according to `PROBE_MODE=block|context`)
- [x] 1.4 [user] With `claude --plugin-dir spike/probe-plugin` in `block` mode, type `/handoff foo`; confirm in the transcript that no new `assistant` entry appears, what the user sees, and whether `suppressOriginalPrompt` works inside `hookSpecificOutput`; record the raw `prompt` field received
- [x] 1.5 [user] In `context` mode, `/handoff foo` must reach the model with the body of `commands/handoff.md` and the `additionalContext`; confirm a large cache read in the transcript's `usage`
- [x] 1.6 [user] In a new session, paste `@/Users/<…>/.claude/handoffs/probe.md` (a test file) and confirm it is included without a `Read` call, and whether it asks for permission; choose `@` or plan B `Read <path>`
- [x] 1.7 Record the findings in `design.md` (D2, D5, D6, D10), adjust the specs if anything changed, and remove `spike/` from the working tree (the branch stays as reference)

## 2. Public repository scaffolding (D11)

- [x] 2.1 `LICENSE` (MIT, same holder as `claude-statusbar`), `.editorconfig` (4 spaces for `*.py`), `.gitattributes`, `.gitignore` (`__pycache__/`, `.venv/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `.DS_Store`)
- [x] 2.2 `pyproject.toml` for dev only (pytest, mypy strict, ruff; `requires-python >= 3.11`, no runtime dependencies) and a `uv.lock`
- [x] 2.3 `Taskfile.yml` with `lint`, `lint:fix`, `typecheck`, `test`, `check` and `dev` (`claude --plugin-dir .`)
- [x] 2.4 `CONTRIBUTING.md`, `CHANGELOG.md` (Keep a Changelog, `[Unreleased]`), `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/bug_report.yml` and `config.yml`
- [x] 2.5 `.github/workflows/ci.yml` (ubuntu/macos/windows × Python 3.11 and 3.13: ruff, mypy, pytest) and `.github/workflows/release.yml` (`v*` tag must match `plugin.json` `version`, run the checks, create a GitHub Release from the `CHANGELOG.md` section)

## 3. Plugin skeleton

- [x] 3.1 `.claude-plugin/plugin.json` (`name: session-handoff`, version `0.1.0`, description, author, repository, license) and `.claude-plugin/marketplace.json` (single plugin, `source: "./"`); confirm the marketplace schema in the docs
- [x] 3.2 `src/session_handoff/constants.py` with the thresholds (50 min / 4 min), caps (400K digest, 24K model, 32K total, 15 error lines, 30 files, 10 pairs kept, 240 s Haiku), base paths and English user-facing strings
- [x] 3.3 `hooks/hooks.json` (`UserPromptSubmit`, empty matcher, `timeout: 300`, shell form `python3 -I "${CLAUDE_PLUGIN_ROOT}/hooks/run.py"`, as used in the spike) and `hooks/run.py`, which adds `src/` to `sys.path` and calls `session_handoff.entry.main`
- [x] 3.4 `commands/handoff.md` with `argument-hint: [--haiku] [focus]` and the two-case body from D3

## 4. Transcript reading and digest (`transcript-digest`)

- [x] 4.1 Synthetic fixtures in `tests/fixtures/` shaped like 2.1.330: sidechain, `<synthetic>`, API error, `thinking`, a response split by `message.id`, two `compact_boundary` entries, ok and error `tool_result`, `usage` with `ephemeral_1h` and `ephemeral_5m`
- [x] 4.2 TDD `transcript.py`: defensive JSONL reading (unreadable lines skipped), main-thread filter, dedupe by `message.id`, cut at the last `compact_boundary` with its summary
- [x] 4.3 TDD `digest.py`: render text and `tool_use` with key parameters, `tool_result` reduced to status plus 15 error lines, file list with read/edit/write counts
- [x] 4.4 TDD `redact.py`: the secret patterns from the spec, applied to the digest before it is saved or sent
- [x] 4.5 TDD digest cap: keep the first user messages and the recent stretch, with a `turns omitted` marker
- [x] 4.6 TDD `git_info.py`: branch, last 5 commits and `status --short`, capped at 40 lines; "no git repository" outside a repository (tests with a temporary repository)

## 5. Command and mode (`handoff-mode`)

- [x] 5.1 TDD `command.py`: the `^\s*/(session-handoff:)?handoff(\s|$)` regex with variants (namespace, arguments, spaces; `/handoffx` does not match), and extraction of `--haiku` and the focus
- [x] 5.2 TDD `mode.py`: TTL from the last request that wrote to the cache, idle time since the last main-thread request, thresholds at the edges (just under and just over 50 min and 4 min), unknown TTL, no `usage`, exception → cold, `--haiku` → cold; the clock is injected

## 6. Handoff document and storage (`handoff-generation`)

- [x] 6.1 TDD `storage.py`: slug from `realpath`, `<timestamp>-<session8>.md` and `.digest.md` paths, retention of 10 pairs per project (tests with a temporary `HOME`)
- [x] 6.2 TDD `handoff_doc.py`: instruction template (fixed English headings, 24K, conversation language, user priority when there is a focus), Haiku answer validation, assembly with `Key files` + `Branch / commits` + the digest line, trimming to 32K in the order the spec gives
- [x] 6.3 TDD `haiku.py`: build the command with the flags from the spike, an environment without `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX`, a temporary `cwd` and a 240 s timeout; errors (not found, non-zero exit, timeout) as a typed exception (tests with a fake executable on `PATH`)

## 7. Delivery and orchestration (`handoff-delivery`)

- [x] 7.1 TDD `clipboard.py`: copy with `pbcopy` when it exists; return `False` without raising when it is missing or fails
- [x] 7.2 TDD `hook_output.py`: the single writer to stdout; block JSON (with `suppressOriginalPrompt` placed as the spike found) and `additionalContext` JSON with the `<session-handoff>` block
- [x] 7.3 TDD `entry.py`: a prompt that is not `/handoff` → no output; cold mode → digest, Haiku, save, message with the exact `@<path>` line; warm mode → digest saved and a block with the path, format, focus and deterministic sections, without blocking; any exception → block with the error and no handoff saved; logging to `~/.claude/handoffs/handoff.log`
- [x] 7.4 Smoke test of `hooks/run.py` as a subprocess with a hook input JSON and a fake `claude` on `PATH` (also wired into CI)

## 8. Real-world validation and docs

- [x] 8.1 Run the extractor on a real transcript from `~/.claude/projects/` (never committed) and review the digest: noise, size, secrets and the file list (8.6 MB compacted session → 160K-char digest, ~40K tokens, compaction summary first, local-command noise later filtered (m7); finding: 251 `Bash` calls vs 1 `Read` left the file list nearly empty → task 8.1a)
- [x] 8.1a TDD `bash_refs.py`: count `Bash` arguments that name existing files inside `cwd` as `bash refs` in the file list and in `Key files` (spec "Files referenced from Bash", design D4)
- [x] 8.2 [user] Install with `claude --plugin-dir .` and try `/handoff` in a session idle for more than an hour: no request in the transcript, a readable handoff, `@<path>` in the clipboard, resume after `/clear` (2026-11-23: cold mode at 1,991 min idle, 4,080-char handoff with all 8 sections, files 0600; resumed by the user)
- [x] 8.3 [user] Try `/handoff <focus>` in a session with a live cache (warm mode) and compare it with `/handoff --haiku` in the same session (2026-11-23: warm chosen at 21 min idle; 9.9K-char handoff by the session's model, more precise than Haiku's (formulas, call chain, reference values); `Write` asked no permission, file 0644 inside the 0700 folder. Findings fixed in 7b73934, 358168c, 3f4e886: warm copied the VERBATIM markers; Haiku contradicted the code-made Branch / commits because it never saw the git state; Key files led with the script being run. Re-run of cold mode on the same transcript after the fixes: 29 s, branch reported from the repository state, no markers, `tools/test.sh` gone from Key files)
- [x] 8.4 Rewrite `README.md` in English: what it is, why (the cache measurements, aggregate only), requirements, install via marketplace, usage (`/handoff [--haiku] [focus]`, `/clear`, `@<path>`), modes and costs, where files go, Windows untested; add the `0.1.0` entry to `CHANGELOG.md`
