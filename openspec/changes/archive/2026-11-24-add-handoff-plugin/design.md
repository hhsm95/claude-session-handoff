## Context

Measurements and the cost reasoning are in `README.md`. In short, the cache expires one TTL after
the last request that read it. After that, any turn in the old session re-writes the whole
context, and `/compact` re-reads it as well.

These facts come from the official Claude Code docs (hooks, plugins, CLI, auth), checked on
2026-11-23, and they constrain the design:

- `UserPromptSubmit` fires for slash commands too, and its `prompt` field holds the raw line the
  user typed. `decision: "block"` rejects the prompt **before the model** sees it.
  `hookSpecificOutput.suppressOriginalPrompt: true` hides the `Original prompt:` echo.
- Instead of blocking, `UserPromptSubmit` can return `additionalContext`, which is added to what
  the model receives.
- Every hook gets `session_id`, `transcript_path` and `cwd`. The transcript is written
  asynchronously, which does not matter for a session idle for an hour.
- No hook can run `/clear` or open a session. The user types `/clear`; the user chose this over
  opening a new terminal window with `osascript`.
- **Nothing is injected on `/clear`.** The user wants `/clear` to behave normally, because not
  every new session will use the handoff. In the new session the user opts in by pasting
  `@<path>`. That also sidesteps the fixed 10,000-character cap on `SessionStart`'s
  `additionalContext`.
- An `@file` mention puts the file's content into that same request, without a `Read` tool call.
  `Read <path>` would take two requests: one to call `Read` and one to answer. The spike confirms
  this.
- The default timeout for `command` hooks is 600 s, except `UserPromptSubmit`, where it is 30 s.
  It is set with `timeout`. `${CLAUDE_PLUGIN_ROOT}` points to the plugin directory.
- `claude -p` uses `ANTHROPIC_API_KEY` when it is set, ahead of the subscription. Calling it from
  a hook is allowed, and child processes get `CLAUDECODE=1`.

## Goals / Non-Goals

**Goals:**
- `/session-handoff:handoff` (`/hand` + Tab; "`/handoff`" below for short) in a session whose cache has expired triggers **no** request to the main model. In
  that case Haiku 5.5 writes the handoff on the user's subscription, with no API key.
- When the cache is almost certainly alive, the session's own model writes the handoff. It
  understands the conversation better than any digest, and the cached read costs cents (warm
  mode, D10).
- `/handoff <focus>` accepts free text that says what to prioritize.
- After `/clear`, the user resumes with a single `@<path>` line. It is shown, and copied to the
  clipboard when `pbcopy` exists.
- `/clear` and session startup are untouched: the plugin registers no `SessionStart` hook.
- Touched files and git state come from code, not from the model's memory.
- Published as a public GitHub repository, following `claude-statusbar`'s conventions (D11).

**Non-Goals:**
- Opening a new session or terminal automatically.
- Other model providers (DeepSeek, etc.) or calling the API with a key.
- Measuring handoff quality; that stays an open question in `README.md`.
- Replacing `/compact`; it is still the way to stay in the same session.

**Anti-goals** (failure even if it technically works):
- A hook failure that lets `/handoff` reach the model and pays the full re-read.
- Picking warm mode with an expired cache: the same re-read cost, caused by a bad estimate.
- A handoff so thin that the new session has to re-explore the repository.
- The plugin changing sessions that did not ask for the handoff.

## Approaches (chosen: 1)

1. **Chosen: the hook handles `/handoff`.** It blocks in cold mode and passes the prompt through
   with instructions in warm mode. The user then runs `/clear` and pastes `@<path>`. It uses only
   documented features, works on every platform, stays in the same terminal and leaves the
   decision to use the handoff with the user.
   - *Variant the user rejected:* `SessionStart(clear)` injecting the handoff automatically. It
     would change every `/clear`, including the ones that are not meant to resume.
2. **The hook opens a new Terminal/iTerm tab with `osascript`.** It is immediate, but macOS-only
   and terminal-specific, and it leaves the old session open, where the user can keep typing by
   mistake.
3. **Rejected: a plain command executed by the model.** That turn re-reads the context without
   cache, which is the cost to avoid.

Haiku runs on the subscription (`claude -p --model haiku`), not an API key. The user chose this,
and it means the plugin needs no extra setup.

## Decisions

### D1. Plugin layout
The repository root is the plugin:

```
.claude-plugin/plugin.json        name: session-handoff
.claude-plugin/marketplace.json   single-plugin marketplace (D11)
commands/handoff.md               command body (see D3)
hooks/hooks.json                  UserPromptSubmit only
hooks/run.py                      entry point: adds src/ to sys.path and calls the package
src/session_handoff/              package (stdlib only, Python >= 3.11, mypy strict)
tests/                            pytest with synthetic transcripts
```

Python, because `tools/` already uses it and it needs nothing installed. The hook runs with
`python3 -I` because it reads transcripts. **My call.**

### D2. Which hook intercepts (my call, after the builder check)
- **Main hook: `UserPromptSubmit`**, with an explicit `timeout: 300`, because this event defaults
  to 30 s.
- **Why not `UserPromptExpansion`:** `UserPromptSubmit` receives the raw text, so it does not
  depend on how Claude Code names a plugin command.
- The script matches `^\s*/(session-handoff:)?handoff(\s|$)`. The hook matcher is empty, so the
  hook runs on every prompt; any other prompt exits at once with no output.
- `UserPromptExpansion` is plan B, if the spike shows that blocking in `UserPromptSubmit` does not
  prevent the request.
- **The spike is the first task of the plan and comes before any product code.** Using a minimal
  plugin, it confirms:
  1. blocking `/handoff` triggers no request: no new `assistant` entry appears in the transcript;
  2. `claude -p --model haiku` answers from inside the hook, on the subscription, with a
     temporary `cwd` and no API key;
  3. the flags that are needed: disabled tools, `--no-session-persistence`, and whether
     `CLAUDECODE` should be removed from the environment;
  4. that `@/absolute/path/outside/the/project.md` (under `~/.claude/handoffs/`) is included in
     the prompt without a `Read` call, and whether it asks for permission. If it does not work,
     plan B is a `Read <path>` line;
  5. that in warm mode the `UserPromptSubmit` `additionalContext` reaches the model together with
     the expanded body of `commands/handoff.md`, and that the turn's `usage` shows a large cache
     read.

  If any of these fails, the design is corrected before going further.

- **Spike results (2026-11-23, Claude Code 2.1.331, branch `spike/hook-probe`):**
  1. Confirmed. The hook received the raw `prompt` `'/session-handoff:handoff foo'` and blocked
     it. No `assistant` entry followed in the transcript. `suppressOriginalPrompt: true` inside
     `hookSpecificOutput` hid the echo, and the user saw
     `UserPromptSubmit operation blocked by hook:` followed by the `reason`.
  2. Confirmed. A nested `claude -p --model haiku` with `CLAUDECODE=1` and no API key answered
     in about 1.9 s through `claude-haiku-5-5` (`authMethod: claude.ai`). With
     `--no-session-persistence` it left no session under `~/.claude/projects/`.
  3. Flags chosen in D5.
  4. Confirmed. `@/Users/…/.claude/handoffs/probe.md` arrived as a `file` attachment, in a single
     request with no `tool_use` and no permission prompt. The UI renders it as
     "Read … (N lines)", but it is not a tool call. `@` stays and plan B is dropped.
  5. Confirmed. The warm turn received the expanded command body plus the `additionalContext`
     and read 55,749 tokens from cache while writing only 374.
- **Finding: plugin commands exist only with their namespace.** A bare `/handoff` is rejected
  locally as "Unknown command" **before** any hook runs, so nothing is sent and it costs nothing.
  The command is `/session-handoff:handoff`; typing `/hand` and accepting the autocomplete
  produces it. The regex still accepts the bare form, which costs nothing and covers a future
  user-level alias. User-facing docs and messages refer to `/session-handoff:handoff`, shown as
  `/hand` + Tab.

### D3. Fail safe: when in doubt, block
- On any pipeline exception the hook still answers `decision: "block"`, with the error in
  `reason`. A failed handoff must never turn into a $2 re-read.
- Failures **outside the pipeline** (Python older than 3.11, the package failing to import, any
  exception escaping `main()`, a failed `emit_block`) are caught by `hooks/run.py`, which uses
  only syntax valid since Python 3.6 and no package import before its checks. It matches the
  command with its own copy of `COMMAND_PATTERN` (a test keeps both equal) on the stdin text it
  read once, writes a one-line reason to stderr and **exits 2**, which blocks a
  `UserPromptSubmit` prompt whatever stdout holds. Other prompts exit 0 silently. Logging setup
  runs inside the protected region, so any exception there also ends in a block.
- The prompt reaches the model only when the hook **explicitly** chooses warm mode (D10). Any
  error while choosing the mode counts as cold.
- `commands/handoff.md` has a two-case body:
  - if the context holds a `<session-handoff>` block from the hook, follow it (warm mode);
  - if it does not, the hook did not run (broken install), and the model answers only
    "The session-handoff hook is not active; check /hooks." without using tools. That turn pays
    the re-read, but it happens only with a broken install, and it makes the problem visible.
- The hook declares `timeout: 300`. The Haiku call has its own 240 s limit; if it is exceeded, the
  hook blocks with an error.

### D4. Extraction (`transcript-digest`)
It follows the "Proposed design §1" section of the original idea in `README.md`:

- Main thread only. Skip `isSidechain: true`, `isApiErrorMessage: true`,
  `message.model == "<synthetic>"`, `thinking` blocks, and entries that are not
  `user`/`assistant`.
- Deduplicate by `message.id`, because one response arrives over several entries.
- If there is a `system` entry with `subtype: "compact_boundary"`, start at the last one. The
  compaction summary that follows it opens the digest.
- Keep user and model text, and each `tool_use` with its name and key parameters (`file_path`,
  `command`, `pattern`, `description`).
- From each `tool_result`, keep the status (ok or error). For an error, keep its first 15 lines;
  for ok, nothing. **My call**: errors matter, file reads do not.
- Build the file list deterministically (path, reads, edits, writes) from `Read`, `Edit`, `Write`
  and `NotebookEdit`, and compute the idle time: minutes since the last main-thread request.
- **One spelling per file.** The file tools carry absolute paths and `Bash` arguments are relative
  to `cwd`, so both sources go through one helper: when `cwd` is known, a path that resolves
  inside it becomes a relative POSIX path, and a path outside it is kept as given. Without `cwd`
  paths are kept as given. The check is isolated per path: a NUL byte, a name too long or a
  symlink loop (`Path.resolve()` raises `RuntimeError` on Python 3.11 and 3.12) means "outside",
  never an error that aborts the handoff.
- **Bash references (added after task 8.1).** A real 8.6 MB session had 251 `Bash` calls and a
  single `Read`, because reading by ranges with `sed -n` or `rtk read` is a common workflow. The
  file list therefore also counts `bash refs`:
  - each `Bash` `command` is split with `shlex`, and an unparsable command counts nothing;
  - every argument that is not a flag and names an existing regular file inside `cwd` (relative
    paths resolve against `cwd`) counts once per command;
  - a token made only of digits is skipped: in `python x.py 2>&1` the `2` and the `1` are file
    descriptors far more often than files named like that, and a rule on the neighbouring
    redirection operator would be more code for the same result;
  - no allowlist of commands, so `rtk read`, `uv run pytest tests/x.py` and `git diff a.py` all
    count;
  - the program token does not count: the first word of each command segment (the command start
    and the first token after `|`, `||`, `&&`, `;`, `&`, `(` or a newline), skipping `NAME=value`
    prefixes. In the first real trial `tools/test.sh res://tests/...` (24 refs) and `build/app.apk`
    ranked above the files actually edited, because the program being run counted as a file.
    `bash tools/test.sh` still counts it: there it is an argument. The newline is a segment
    separator, so `shlex` runs with newline as punctuation and not as blank;
  - the figure is kept apart from reads, because it is an inference and not a confirmed read.
    It is shown only when non-zero.
- **Key files ranking.** By weighted score, not by raw total: writes and edits 3, reads 2, bash
  refs 1 (constants). A mention in a command is weaker evidence than a read, and a read weaker
  than a change. Ties keep first-touch order; the lines show the raw counts.
- Git state of `cwd`, only inside a repository: branch, `git log --oneline -5` and
  `git status --short`, capped at 40 lines.
- Basic secret redaction before anything is sent or saved: values after `KEY=`, `TOKEN=`,
  `SECRET=` or `PASSWORD=`, `Bearer …`, and the `sk-…`, `ghp_…` and `glpat-…` patterns. Haiku is
  the same provider, but the handoff stays on disk.
- Input cap for Haiku: about 400,000 characters. Beyond that, keep the first user messages (the
  goal) and the most recent stretch, and replace the middle with `[… N turns omitted …]`.
  **My call.**

### D5. Cold generation (`handoff-generation`)
- Command, as confirmed by the spike:
  `claude -p --model haiku --output-format json --tools "" --no-session-persistence --strict-mcp-config --setting-sources "" --system-prompt <template>`,
  with on stdin a `## Repository state (authoritative, generated by code)` section (the same text
  as `Branch / commits`, redacted and neutralized like the digest) followed by the digest. The
  git block is appended to the handoff after Haiku answers, so without that section Haiku never
  sees it: in the first cold trial it wrote "no open branches" while `Branch / commits` showed a
  feature branch with a fresh fix commit. **My call:** the saved digest file stays without the
  section (it is the transcript digest, and the handoff already carries the git text), so git is
  computed once and only the Haiku input gets it.
  - `--output-format json` returns `result`, `is_error` and `modelUsage`, so failures are
    detected without parsing text.
  - `--tools ""` means the call cannot act.
  - `--no-session-persistence` means no session is added to the project's `--resume` list.
  - `--strict-mcp-config` and `--setting-sources ""` skip MCP servers and user settings. That
    also means `enabledPlugins` is not loaded, so this plugin's hook does not run inside the
    nested call.
  - **Not `--bare`:** it skips keychain reads, which breaks the subscription login.
- The environment has none of `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`,
  `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX`, to force the subscription.
- The call runs in a temporary `cwd`.
- The template asks for the fixed format from `README.md §3`. Headings are in English so they can
  be validated; the body is written in the conversation's language.

  ```
  # Handoff: <topic>
  ## Goal
  ## Current state
  ## Decisions and why
  ## Ruled out and why
  ## Next steps
  ## How to verify
  ```

  It also asks for **at most 24,000 characters** (about 6K tokens) and forbids inventing files.
  Two rules are for Haiku only: the end of the conversation is the current state (later turns
  override earlier ones), and the Repository state section is authoritative for branches,
  commits and uncommitted changes and is never contradicted. The warm block does not carry them:
  the session model has the conversation and the live state.
- **User focus:** the text after `/handoff`, without the D10 flags, goes into the template as
  `User priority: …`. The template asks to order `Goal` and `Next steps` by that focus and to drop
  whatever the user marks as irrelevant. Without focus text, nothing is added.
- After the model's answer, the code appends the deterministic sections `## Key files` (top 30 by
  activity) and `## Branch / commits`, plus a last line:
  `Full detail (read only if needed): <digest_path>`. The total cap is 32,000 characters, about
  8K tokens, the README's 5–10K target. Over it, `Key files` is trimmed first, then the end of the
  model text. **My call.**
- Answer validation (**my call**): it must start with `# Handoff` and contain at least `Goal`,
  `Current state` and `Next steps`. If the answer is empty, a refusal or missing those sections,
  the hook blocks with an error. The digest is still saved for debugging.
- Two files are saved under `~/.claude/handoffs/<cwd-slug>/`:
  - `<timestamp>-<session8>.md`: the handoff;
  - `<timestamp>-<session8>.digest.md`: the full digest, read on demand.
- `slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))`, the same shape Claude Code uses
  under `~/.claude/projects/`. **My call.**

### D6. Resuming (`handoff-delivery`)
- In cold mode, the block shows the user a `reason` like this:

  ```
  Handoff ready with Haiku (N chars, ~T tokens; idle M min).
  To resume: /clear, then paste this line (already in your clipboard):
  @<absolute handoff path>
  ```

- The `@<path>` line stands alone, with an absolute path, so it can be copied as is. The spike
  confirmed that `@` works for paths outside the project, as a single-request attachment.
- **Clipboard:** if `pbcopy` is on `PATH`, the hook copies the `@<path>` line and the message says
  so. If it is missing or fails, that phrase is silently dropped. No other clipboard tools
  (`xclip`, `wl-copy`). **My call**: the maintainer works on macOS, and contributors can add more.
- Block output:
  `{"decision": "block", "reason": "…", "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "suppressOriginalPrompt": true}}`.
  The spike confirmed this placement of `suppressOriginalPrompt`.
- No state between sessions: no pending file and no `SessionStart` hook. If the user does not
  paste the line, the new session is a normal session.

### D7. Outputs and logging
- Hook outputs are JSON on stdout, written from a single module.
- Diagnostics go through `logging` to `~/.claude/handoffs/handoff.log`. No `print`.
- User-facing strings, paths and limits live in `constants.py`. User-facing strings are in
  English, as in `claude-statusbar`.

### D8. Retention (my call)
The last 10 handoff/digest pairs per project are kept; older ones are deleted when a new one is
generated. Regex redaction is the only secret protection. The digest is always saved, because it
is what lets the new session read the full detail.

### D9. Fixtures
Tests use synthetic transcripts shaped like Claude Code 2.1.330. A manual task runs the extractor
on a real transcript from `~/.claude/projects/` and reviews the digest. Real transcripts are
**never** committed, because they are private.

### D10. Warm or cold (`handoff-mode`)
**Choosing the mode** (a pure function, tested with TDD):
- The effective TTL comes from the last main-thread request that **wrote** to the cache: `5m` if
  `cache_creation.ephemeral_5m_input_tokens > 0` (even when it also wrote 1h tokens, because the
  5m part is the conversation tail and expires first), `1h` if only
  `ephemeral_1h_input_tokens > 0`.
- Idle time is measured from the `timestamp` of the last main-thread **request** (the last entry
  that carried `usage`) to now. Entries that sent no request (bash-mode `<bash-input>` and
  `<bash-stdout>`, local commands with `<command-name>` or `<local-command-stdout>`, `isMeta`
  entries, a blocked prompt) must not refresh it.
- The mode is **warm** only when both hold: the TTL is known, and the idle time is under that
  TTL's threshold (**50 min** for `1h`, **4 min** for `5m`). Any other result (unknown TTL,
  unreadable transcript, an exception) is **cold**. The thresholds live in `constants.py`.
- **My call:** the 10-minute and 1-minute margins absorb clock skew and requests the transcript
  does not show yet, because it is written asynchronously.
- **Flags:** `/handoff --haiku …` forces cold. There is no flag to force warm, because forcing it
  with an expired cache is exactly the anti-goal. **My call.**

**Cold mode:** the D5 and D6 flow, with a block.

**Warm mode:**
- The hook does **not** block. It computes and saves the deterministic digest, so the pointer to
  the full detail is valid.
- It returns `hookSpecificOutput.additionalContext` with a `<session-handoff>` block containing:
  - the target path of the handoff;
  - the D5 fixed format and the 24,000-character limit;
  - the user focus, if any;
  - the deterministic `Key files` and `Branch / commits` sections, to be copied verbatim at the
    end, plus the digest line. The two marker lines around that text are delimiters: the
    instruction says to write only what is between them, "without the two marker lines". (In the
    first trial the model copied the markers into the file.)
  - the closing instruction, placed **before** the format and the verbatim text so it is not
    lost: write the file with `Write`, then answer with "Handoff ready. To resume: /clear, then
    paste this line:" and the `@<path>` alone on its own line, without backticks;
  - the whole `additionalContext` stays under 9,500 characters (Claude Code spills output over
    10,000 into a file), measured on the final wrapped text. The focus is capped at 1,000
    characters inside the block, key files are trimmed first, as in D5, and the git text is
    shortened last, so the bound holds for any input.
- Before returning, it copies `@<path>` to the clipboard, as in D6.
- The body of `commands/handoff.md` tells the model to follow that block (D3).
- **Expected cost:** one request that reads the whole context from cache, plus about 6K output
  tokens, and a short second request after `Write`, also cached. Cents, against about $2 for the
  re-read.
- The `Write` to `~/.claude/handoffs/` may ask for permission, because it is outside the project.
  That is accepted. In the first warm trial (task 8.3, 2026-11-23, in the user's permission mode)
  it did not ask. Other permission modes may ask once. The file it creates gets the user's umask
  (0644 in that trial), and the 0700 folder keeps other users out.

### D11. Public repository (follows `claude-statusbar`)
- English everywhere public: `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, OpenSpec artifacts,
  code comments and user-facing strings. The Spanish idea notes in the current `README.md` are
  rewritten in English as the README's "Why" section.
- MIT `LICENSE`, with the same holder as `claude-statusbar`.
- `.editorconfig` (LF, UTF-8, 2 spaces; 4 for `*.py`), `.gitattributes` (`* text=auto eol=lf`)
  and `.gitignore`.
- `CHANGELOG.md` in Keep a Changelog format, with SemVer starting at `0.1.0`.
- `CONTRIBUTING.md` with the flow, the dev setup (`uv`), the rules (stdlib only at runtime, the
  hook never lets `/handoff` through on error, strict mypy, no barrel files, English strings) and
  Conventional Commits with one commit per PR.
- `.github/`: `pull_request_template.md`, `ISSUE_TEMPLATE/bug_report.yml` (Claude Code version,
  Python version, OS, `handoff.log` excerpt with private values removed) and `config.yml`.
- `.github/workflows/ci.yml`: push to `main` and pull requests, a matrix of
  ubuntu/macos/windows × Python 3.11 and 3.13, running ruff, mypy and pytest, plus a smoke test
  of `hooks/run.py` with a fake `claude`.
- `.github/workflows/release.yml`: on `v*` tags, check that the tag matches `plugin.json`'s
  `version`, run the checks and create a GitHub Release with the `CHANGELOG.md` section.
- `.claude-plugin/marketplace.json` with the single plugin, so users install it with
  `/plugin marketplace add hhsm95/claude-session-handoff` and then
  `/plugin install session-handoff@<marketplace>`. The exact commands are confirmed in the docs
  while writing the README.
- `Taskfile.yml` wraps the `uv run` commands (`lint`, `typecheck`, `test`, `check`), plus `dev`,
  which starts `claude --plugin-dir .`.
- The OpenSpec `.claude/` files (`opsx` commands and skills) are committed, as in
  `claude-statusbar`.
- Windows: CI runs the tests there. At runtime the hook calls `python3`, which may not exist on
  Windows, so the README marks Windows as untested. **My call.**

## Risks / Trade-offs

- [Blocking in `UserPromptSubmit` does not prevent the request, or the text does not arrive raw]
  → the spike catches it before any code is written. Plan B: `UserPromptExpansion`.
- [The regex misses the command and the body reaches the model, paying the full re-read] → the
  regex is tested against variants with and without the namespace and with arguments. The
  fallback body says plainly that the hook failed, so the user does not retry.
- [Warm mode is chosen with an expired cache, and one `/handoff` pays the full re-read] →
  conservative thresholds, any doubt means cold, and `--haiku` forces cold. The mode decision is
  tested at the edges: just under and just over the threshold, a `5m` TTL, an unknown TTL and a
  transcript with no `usage`.
- [The hook is killed by its timeout, the prompt reaches the model, and a stale `<session-handoff>`
  block from an earlier warm run is still in context] → the time budget is pinned by a test well
  under the hook timeout; accepted residual risk.
- [The warm `Write` creates the handoff with the user's umask, so it may be readable by others]
  → Claude Code creates the file, not the hook; the 0700 project folder mitigates it.
- [`claude -p` expands `@path` mentions in stdin, so a prompt such as `@/etc/hosts` would attach
  that file to the Haiku call] → the digest gets U+2060 after every mention-like `@` (one not
  preceded by a word character, so emails and `git@host` URLs stay intact), in what is sent and
  what is saved.
- [In warm mode the model does not follow the block, e.g. writes elsewhere or over-summarizes] →
  the block is short and explicit and the path is precomputed. If the file is missing at the end,
  the user sees it, because the answer has no `@<path>` line.
- [No subscription available to `claude -p` (no `/login`)] → the hook blocks with a clear
  message. There is no API-key fallback; it is a non-goal.
- [The handoff loses detail] → the full digest stays on disk, and the handoff's last line points
  to it.
- [The nested `claude -p` loads the plugin and its `UserPromptSubmit` hook] → the prompt sent to
  Haiku does not start with `/handoff`, so the hook exits immediately. The spike confirms it.
- [The transcript format changes in another Claude Code version] → defensive reading in the style
  of `claude-statusbar/src/transcript.ts`: unreadable entries are skipped, and an empty digest
  blocks with an error.
- [Personal data in a public repository] → fixtures are synthetic, and the README keeps only
  aggregate measurements, never transcript content or local paths.

## Open Questions

- All spike questions are settled (D2), and so is warm mode's `Write` (see Risks). Still open:
  how the warm handoff compares with `--haiku` on the same session (task 8.3).
