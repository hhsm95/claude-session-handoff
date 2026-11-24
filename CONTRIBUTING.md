# Contributing to claude-session-handoff

Thanks for helping out. Bug reports and fixes are welcome.

## Flow

1. For a larger change, open an issue first so we can agree on the approach before you write code.
2. Fork the repository and create a branch (`feat/...`, `fix/...`, `docs/...`).
3. Make your change, with tests.
4. Open a pull request against `main` and fill in the template.

## Development setup

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # install the dev tools from uv.lock
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict type-check
uv run pytest -q             # tests
```

With [Task](https://taskfile.dev) installed, `task check` runs all of the above and `task dev`
starts Claude Code with this checkout loaded as a plugin (`claude --plugin-dir .`).

## Rules

- Standard library only at runtime. Dev tools (pytest, mypy, ruff) are the only dependencies.
- The hook must never let `/session-handoff:handoff` reach the model on error: when anything
  fails, it blocks the prompt and shows the error instead.
- mypy runs in `strict` mode; do not use `Any`. No barrel files: `__init__.py` stays empty and
  imports are explicit.
- User-facing strings are in English and live in `constants.py`.
- No `print`. Use `logging` for diagnostics; stdout is written only by the hook output module.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): description`,
  for example `fix(digest): skip sidechain entries`.
- One commit per pull request; squash before asking for review.
- The checks above must pass. CI runs them on Linux, macOS and Windows.
