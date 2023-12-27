"""Configuration, loaded from TOML and validated on the way in.

The auction changes its own rules more often than it changes its software. Fee
tiers move every January, the arbitration windows were renegotiated with the
dealer council twice last year, and the list of days we are shut is different
from everyone else's. All of that belongs in a file an operations manager can
edit, and none of it belongs in a constant.

Unknown keys are an error rather than a shrug. A misspelt setting that is
silently ignored looks exactly like a setting that did not work, and the last
time that happened it was three sales before anyone noticed the wrong fee tier.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date, time
from pathlib import Path
from typing import Any

from autozest.errors import ConfigError
from autozest.timing import BusinessCalendar

DEFAULT_FILENAME = "autozest.toml"


@dataclass(frozen=True)
class Settings:
    """Everything the application needs before it opens a database."""

    auction_name: str = "Autozest Remarketing"
    auction_code: str = "AZ01"
    database: str = "autozest.sqlite3"
    opens: time = time(8, 0)
    closes: time = time(17, 30)
    extra_closures: tuple[date, ...] = ()
    extra_openings: tuple[date, ...] = ()
    sections: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def calendar(self) -> BusinessCalendar:
        return BusinessCalendar(
            opens=self.opens,
            closes=self.closes,
            extra_closures=frozenset(self.extra_closures),
            extra_openings=frozenset(self.extra_openings),
        )

    def section(self, name: str) -> Mapping[str, Any]:
        """One subsystem's settings, empty when the file does not mention it."""
        return self.sections.get(name, {})

    def database_path(self, relative_to: Path | None = None) -> Path:
        base = relative_to or Path.cwd()
        return (
            (base / self.database).resolve()
            if not Path(self.database).is_absolute()
            else Path(self.database)
        )


#: Top level keys that map onto a Settings field rather than a policy section.
_SCALAR_KEYS = {f.name for f in fields(Settings)} - {"sections"}

#: Section names the rest of the package knows how to read.
KNOWN_SECTIONS = frozenset(
    {"arbitration", "fees", "block", "title", "settlement", "inspection"}
)


def _parse_time(value: Any, key: str) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{key} is not a time: {value!r}") from exc
    raise ConfigError(f"{key} should be a time, got {type(value).__name__}")


def _parse_dates(value: Any, key: str) -> tuple[date, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{key} should be a list of dates")
    parsed: list[date] = []
    for item in value:
        if isinstance(item, date):
            parsed.append(item)
        elif isinstance(item, str):
            try:
                parsed.append(date.fromisoformat(item))
            except ValueError as exc:
                raise ConfigError(
                    f"{key} holds something that is not a date: {item!r}"
                ) from exc
        else:
            raise ConfigError(f"{key} holds a {type(item).__name__}")
    return tuple(parsed)


def from_mapping(raw: Mapping[str, Any]) -> Settings:
    """Build settings from an already parsed mapping."""
    scalars: dict[str, Any] = {}
    sections: dict[str, Mapping[str, Any]] = {}

    for key, value in raw.items():
        if isinstance(value, dict):
            if key not in KNOWN_SECTIONS:
                raise ConfigError(
                    f"[{key}] is not a section this version knows about",
                    known=sorted(KNOWN_SECTIONS),
                )
            sections[key] = value
            continue
        if key not in _SCALAR_KEYS:
            raise ConfigError(f"{key!r} is not a setting", known=sorted(_SCALAR_KEYS))
        if key in ("opens", "closes"):
            scalars[key] = _parse_time(value, key)
        elif key in ("extra_closures", "extra_openings"):
            scalars[key] = _parse_dates(value, key)
        elif not isinstance(value, str):
            raise ConfigError(f"{key} should be text, got {type(value).__name__}")
        else:
            scalars[key] = value

    settings = Settings(**scalars, sections=sections)
    if settings.opens >= settings.closes:
        raise ConfigError("the auction closes before it opens", opens=str(settings.opens))
    return settings


def load(path: Path | str | None = None) -> Settings:
    """Read a configuration file, or return the defaults when there is none.

    A path that was asked for explicitly and is not there is an error. The
    default filename simply not existing is not.
    """
    if path is None:
        candidate = Path.cwd() / DEFAULT_FILENAME
        if not candidate.exists():
            return Settings()
    else:
        candidate = Path(path)
        if not candidate.exists():
            raise ConfigError(f"no configuration file at {candidate}", path=str(candidate))
    try:
        raw = tomllib.loads(candidate.read_text("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{candidate} is not valid TOML: {exc}", path=str(candidate)) from exc
    return from_mapping(raw)
