"""What every hook handler shares: its injectable dependencies, the base folder, the file log."""

import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from session_handoff.constants import (
    HANDOFFS_DIR,
    HOOK_INPUT_MISSING_FIELD_TEMPLATE,
    HOOK_INPUT_NOT_OBJECT_MESSAGE,
    LOGGER_NAME,
    PLUGIN_VERSION,
    PROJECTS_DIR,
    RETENTION_FAILURE_LOG_MESSAGE,
)
from session_handoff.storage import apply_retention, ensure_private_dir, open_private_file
from session_handoff.transcript import TimelineItem, read_timeline

LOGGER = logging.getLogger(LOGGER_NAME)
_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


@dataclass(frozen=True)
class HookDependencies:
    now: Callable[[], datetime]
    base_dir: Path | None
    run_haiku: Callable[[str, str], str]
    copy_to_clipboard: Callable[[str], bool]
    read_timeline: Callable[[Path], tuple[TimelineItem, ...]] = read_timeline
    plugin_version: str = PLUGIN_VERSION
    projects_dir: Path = Path(PROJECTS_DIR).expanduser()


def base_dir_of(dependencies: HookDependencies) -> Path:
    if dependencies.base_dir is not None:
        return dependencies.base_dir
    return Path(HANDOFFS_DIR).expanduser()


def string_fields(payload: object, keys: tuple[str, ...]) -> dict[str, str]:
    """The named string fields of a hook payload; a payload that lacks one raises `ValueError`."""
    if not isinstance(payload, dict):
        raise ValueError(HOOK_INPUT_NOT_OBJECT_MESSAGE)
    fields = {}
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str):
            raise ValueError(HOOK_INPUT_MISSING_FIELD_TEMPLATE.format(field=key))
        fields[key] = value
    return fields


@contextmanager
def file_logging(log_path: Path) -> Iterator[None]:
    """Log to `log_path` while the pipeline runs; without a writable file, log nowhere."""
    try:
        ensure_private_dir(log_path.parent)
        os.close(open_private_file(log_path, os.O_WRONLY | os.O_APPEND))
        handler: logging.Handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    try:
        yield
    finally:
        LOGGER.removeHandler(handler)
        handler.close()


def apply_retention_safely(project_dir: Path) -> None:
    """Retention is housekeeping: failing must not turn a produced handoff into a failure."""
    try:
        apply_retention(project_dir)
    except Exception:
        LOGGER.exception(RETENTION_FAILURE_LOG_MESSAGE)
