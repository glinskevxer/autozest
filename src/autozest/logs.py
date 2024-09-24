"""Logging, as key and value pairs on one line.

Not JSON. The people who read this log read it in a terminal at half past six
in the morning on a machine with no tooling on it, and a wall of JSON is worse
for them than a wall of prose. Key and value pairs are greppable, sortable with
cut, and still readable by eye, which is the whole set of requirements.

The one rule that matters: **a value that could contain a space is quoted, and
a value that could contain a quote is escaped**. A log line that cannot be
parsed reliably is a log line nobody trusts, and the first time somebody greps
for `vin=` and gets half a message the log stops being used.

Money, dates and enums are rendered here rather than by the caller, so the same
kind of thing looks the same wherever it is logged.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.money import Money

LOGGER_NAME = "autozest"

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def render_value(value: Any) -> str:
    """One rule for how each kind of thing looks in a log line."""
    if value is None:
        return "-"
    if isinstance(value, Money):
        return str(value.dollars)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if not text:
        return '""'
    if any(character in text for character in ' ="\n\t'):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        return f'"{escaped}"'
    return text


def render(event: str, fields: Mapping[str, Any]) -> str:
    """An event name followed by its fields, in the order they were given."""
    pairs = " ".join(f"{key}={render_value(value)}" for key, value in fields.items())
    return f"{event} {pairs}".strip()


class Log:
    """A thin wrapper that formats before it hands anything to logging."""

    def __init__(self, name: str = LOGGER_NAME) -> None:
        self._logger = logging.getLogger(name)

    def debug(self, event: str, **fields: Any) -> None:
        self._logger.debug(render(event, fields))

    def info(self, event: str, **fields: Any) -> None:
        self._logger.info(render(event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self._logger.warning(render(event, fields))

    def error(self, event: str, **fields: Any) -> None:
        self._logger.error(render(event, fields))

    @property
    def level(self) -> int:
        return self._logger.getEffectiveLevel()

    def is_enabled(self, level: str) -> bool:
        return self._logger.isEnabledFor(LEVELS[level])


class LineFormatter(logging.Formatter):
    """Timestamp, level, then the rendered event."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).astimezone()
        level = record.levelname.lower()
        return f"{stamp.isoformat(timespec='seconds')} {level:<7} {record.getMessage()}"


def configure(level: str = "warning", stream: Any = None) -> None:
    """Point the package logger at a stream, once.

    Called from the command line. A library that configures logging on import
    is a library that fights whatever is embedding it, so nothing here happens
    until somebody asks.
    """
    if level not in LEVELS:
        raise ValueError(f"{level!r} is not a log level, try {', '.join(sorted(LEVELS))}")
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(LineFormatter())
    logger.addHandler(handler)
    logger.setLevel(LEVELS[level])
    logger.propagate = False


log = Log()
