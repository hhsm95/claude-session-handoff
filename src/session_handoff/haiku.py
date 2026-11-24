"""Cold generation: run `claude -p --model haiku` on the digest, on the user's subscription."""

import json
import os
import subprocess
import tempfile

from session_handoff.constants import (
    HAIKU_CLI_ARGS,
    HAIKU_EXIT_TEMPLATE,
    HAIKU_NO_RESULT_MESSAGE,
    HAIKU_NOT_FOUND_TEMPLATE,
    HAIKU_NOT_JSON_MESSAGE,
    HAIKU_REPORTED_ERROR_TEMPLATE,
    HAIKU_STDERR_MAX_CHARS,
    HAIKU_STRIPPED_ENV_VARS,
    HAIKU_TIMEOUT_SECONDS,
    HAIKU_TIMEOUT_TEMPLATE,
)


class HaikuError(Exception):
    """The Haiku call failed; the message says how, in terms the user can act on."""


def run_haiku(
    digest: str,
    system_prompt: str,
    *,
    timeout_seconds: float = HAIKU_TIMEOUT_SECONDS,
    executable: str = HAIKU_CLI_ARGS[0],
) -> str:
    """The model's answer to the digest under the system prompt, or `HaikuError`."""
    command = [executable, *HAIKU_CLI_ARGS[1:], system_prompt]
    environment = {k: v for k, v in os.environ.items() if k not in HAIKU_STRIPPED_ENV_VARS}
    # A neutral cwd keeps the nested call from picking up the project's CLAUDE.md and settings.
    with tempfile.TemporaryDirectory() as neutral_cwd:
        try:
            completed = subprocess.run(
                command,
                input=digest,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=neutral_cwd,
                env=environment,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise HaikuError(HAIKU_NOT_FOUND_TEMPLATE.format(executable=executable)) from error
        except subprocess.TimeoutExpired as error:
            message = HAIKU_TIMEOUT_TEMPLATE.format(seconds=timeout_seconds)
            raise HaikuError(message) from error
    if completed.returncode != 0:
        stderr = completed.stderr.strip()[-HAIKU_STDERR_MAX_CHARS:]
        raise HaikuError(HAIKU_EXIT_TEMPLATE.format(code=completed.returncode, stderr=stderr))
    return _result_of(completed.stdout)


def _result_of(stdout: str) -> str:
    try:
        answer = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise HaikuError(HAIKU_NOT_JSON_MESSAGE) from error
    if not isinstance(answer, dict) or not isinstance(answer.get("result"), str):
        raise HaikuError(HAIKU_NO_RESULT_MESSAGE)
    if answer.get("is_error") is True:
        raise HaikuError(HAIKU_REPORTED_ERROR_TEMPLATE.format(result=answer["result"]))
    return str(answer["result"])
