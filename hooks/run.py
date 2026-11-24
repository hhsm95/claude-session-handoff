"""Hook entry point: put the plugin's `src/` on `sys.path` and run the package.

Keep this file valid for old Python grammar (3.6+) and free of package imports at the top: it must
be able to say "blocked" even when the package cannot load, because a `/handoff` that reaches the
model costs a full uncached re-read.
"""

import importlib
import json
import re
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)
# Same pattern as `constants.COMMAND_PATTERN`; a test keeps them equal.
COMMAND_PATTERN = r"^\s*/(session-handoff:)?handoff(\s|$)"
BLOCKING_EXIT_CODE = 2  # exit 2 blocks a UserPromptSubmit prompt whatever stdout holds
FAILURE_MESSAGE = (
    "session-handoff failed before it could run ({error}). "
    "The /handoff prompt was blocked; check python3 >= 3.11 and the plugin install."
)


def run(raw_input: str) -> None:
    if sys.version_info < MIN_PYTHON:
        raise RuntimeError("python3 {}.{} or newer is required".format(*MIN_PYTHON))
    plugin_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(plugin_root / "src"))
    importlib.import_module("session_handoff.entry").main(raw_input)


def is_handoff_command(raw_input: str) -> bool:
    try:
        payload = json.loads(raw_input)
        return re.match(COMMAND_PATTERN, payload["prompt"]) is not None
    except Exception:
        return False


def main() -> None:
    raw_input = ""
    try:
        raw_input = sys.stdin.buffer.read().decode("utf-8", "replace")
        run(raw_input)
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        # Deliberately BaseException: a `SystemExit` raised inside the pipeline must block a
        # `/handoff` too, since any exit other than 2 lets the prompt reach the model. For the
        # same reason a successful run has to return normally and never call `sys.exit(0)`.
        if is_handoff_command(raw_input):
            reason = str(error) or type(error).__name__
            sys.stderr.write(FAILURE_MESSAGE.format(error=reason) + "\n")
            sys.exit(BLOCKING_EXIT_CODE)


if __name__ == "__main__":
    main()
