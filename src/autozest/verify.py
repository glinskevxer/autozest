"""One command that runs everything that can be checked.

Five separate checks already exist and every one of them is run by somebody
different at a different time. That is the problem: the person who runs the
health check is not the person who runs the doctor, and neither of them runs
the release checks.

So this runs all of them and gives one answer. It changes nothing, which is
what lets it go in a cron job, and it separates what stops the building from
what somebody should look at this week, because those go to different people
at different hours.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

from autozest.config import Settings
from autozest.health import Severity
from autozest.health import examine as examine_health
from autozest.tools.advisories import overdue as overdue_advisories
from autozest.tools.doctor import examine as examine_database
from autozest.tools.release import checks as release_checks


class Level(Enum):
    """What a finding means for whoever is reading."""

    STOP = "stop"
    LOOK = "look"

    @property
    def wakes_somebody(self) -> bool:
        return self is Level.STOP


@dataclass(frozen=True)
class Result:
    """One thing that was checked."""

    source: str
    name: str
    passed: bool
    detail: str
    level: Level = Level.STOP

    @property
    def mark(self) -> str:
        """Four characters somebody can grep for and eyeball in a column."""
        if self.passed:
            return "ok  "
        return "STOP" if self.level is Level.STOP else "look"

    def __str__(self) -> str:
        return f"{self.mark}  {self.source}/{self.name}: {self.detail}"


@dataclass(frozen=True)
class Verification:
    """Everything, from every check there is."""

    results: tuple[Result, ...]

    @property
    def failures(self) -> tuple[Result, ...]:
        return tuple(result for result in self.results if not result.passed)

    @property
    def blocking(self) -> tuple[Result, ...]:
        return tuple(result for result in self.failures if result.level is Level.STOP)

    @property
    def advisory(self) -> tuple[Result, ...]:
        return tuple(result for result in self.failures if result.level is Level.LOOK)

    @property
    def is_usable(self) -> bool:
        return not self.blocking

    def summary(self) -> str:
        if not self.failures:
            return f"{len(self.results)} checks, everything passes"
        return (
            f"{len(self.results)} checks, {len(self.blocking)} stopping, "
            f"{len(self.advisory)} to look at"
        )


def verify(conn: sqlite3.Connection, settings: Settings | None = None) -> Verification:
    """Run every check there is against this database and this build."""
    results: list[Result] = []

    for check in examine_health(conn, settings).checks:
        results.append(
            Result(
                source="health",
                name=check.name,
                passed=check.passed,
                detail=check.detail,
                level=Level.STOP if check.severity is Severity.HARD else Level.LOOK,
            )
        )

    report = examine_database(conn)
    for finding in report.findings:
        results.append(
            Result(
                source="doctor",
                name=finding.code,
                passed=False,
                detail=f"{finding.detail} ({finding.rows})",
                level=Level.LOOK if finding.repairable else Level.STOP,
            )
        )
    if report.is_clean:
        results.append(
            Result("doctor", "shape", True, "nothing wrong with the shape", Level.STOP)
        )

    for release_check in release_checks():
        results.append(
            Result(
                source="release",
                name=release_check.name,
                passed=release_check.passed,
                detail=release_check.detail,
                level=Level.STOP,
            )
        )

    stale = overdue_advisories()
    results.append(
        Result(
            source="advisories",
            name="acceptances",
            passed=not stale,
            detail=f"{len(stale)} acceptances are past their review date",
            level=Level.LOOK,
        )
    )
    return Verification(results=tuple(results))


def render(conn: sqlite3.Connection, settings: Settings | None = None) -> list[str]:
    result = verify(conn, settings)
    lines = [str(item) for item in result.results]
    lines.append(result.summary())
    return lines
