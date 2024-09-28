"""Is this installation working.

One function, a list of checks, and a straight answer. It exists because the
question "is it broken" gets asked at six in the morning by somebody who is not
going to read a stack trace, and the honest answer has three parts: what was
checked, what it found, and whether the building can open.

Checks are split into hard and soft. A hard failure means the system cannot be
used and somebody has to be woken up. A soft one means something will go wrong
later, which is a different conversation and a different hour of the day.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from enum import Enum

from autozest.config import Settings
from autozest.settlement.tax import TaxService
from autozest.storage.db import foreign_keys_are_on, integrity_problems
from autozest.storage.migrator import current_version, load_migrations, pending
from autozest.storage.records import scalar
from autozest.tools.check_migrations import problems as migration_problems


class Severity(Enum):
    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True)
class Check:
    """One thing that was looked at."""

    name: str
    passed: bool
    detail: str
    severity: Severity = Severity.HARD

    def __str__(self) -> str:
        mark = "ok" if self.passed else self.severity.value.upper()
        return f"{mark:<4} {self.name}: {self.detail}"


@dataclass(frozen=True)
class Health:
    """Everything that was looked at, and the verdict."""

    checks: tuple[Check, ...]

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    @property
    def hard_failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.failures if check.severity is Severity.HARD)

    @property
    def can_open(self) -> bool:
        """Whether the building can trade this morning."""
        return not self.hard_failures

    def summary(self) -> str:
        if self.can_open:
            soft = len(self.failures)
            tail = f", {soft} things to look at" if soft else ""
            return f"healthy{tail}"
        return f"not usable, {len(self.hard_failures)} hard failures"


def examine(
    conn: sqlite3.Connection, settings: Settings | None = None, *, today: date | None = None
) -> Health:
    """Look at everything and say whether the building can open."""
    day = today or date.today()
    config = settings or Settings()
    checks: list[Check] = []

    damage = integrity_problems(conn)
    checks.append(
        Check("database", not damage, damage[0] if damage else "no damage and no orphans")
    )
    checks.append(
        Check("foreign keys", foreign_keys_are_on(conn), "enforced on this connection")
    )

    outstanding = pending(conn)
    checks.append(
        Check(
            "schema",
            not outstanding,
            f"at {current_version(conn)} of {len(load_migrations())}",
        )
    )

    bad_migrations = migration_problems()
    checks.append(
        Check(
            "migrations",
            not bad_migrations,
            bad_migrations[0] if bad_migrations else "shipped migrations are sound",
        )
    )

    unbalanced = int(
        scalar(
            conn,
            "SELECT COALESCE(SUM(debit_cents), 0) - COALESCE(SUM(credit_cents), 0) "
            "FROM ledger_line",
        )
        or 0
    )
    checks.append(
        Check(
            "ledger",
            unbalanced == 0,
            "balanced" if unbalanced == 0 else f"out by {unbalanced} cents",
        )
    )

    checks.append(_calendar_check(config, day))
    checks.append(_tax_check(conn))
    checks.append(_notice_check(conn))
    return Health(checks=tuple(checks))


def _calendar_check(settings: Settings, day: date) -> Check:
    calendar = settings.calendar
    upcoming = [
        candidate for candidate in (day.replace(day=1),) if calendar.is_business_day(candidate)
    ]
    return Check(
        "calendar",
        True,
        f"opens {calendar.opens} closes {calendar.closes}, "
        f"{len(upcoming)} of the first of the month is a working day",
        Severity.SOFT,
    )


def _tax_check(conn: sqlite3.Connection) -> Check:
    service = TaxService(conn)
    expiring = service.expiring(date.today())
    return Check(
        "certificates",
        not expiring,
        f"{len(expiring)} exemption certificates have lapsed",
        Severity.SOFT,
    )


def _notice_check(conn: sqlite3.Connection) -> Check:
    stuck = int(
        scalar(conn, "SELECT COUNT(*) FROM notice WHERE status = 'failed' AND attempts >= 3")
        or 0
    )
    return Check(
        "notices",
        stuck == 0,
        f"{stuck} notices have failed too many times",
        Severity.SOFT,
    )
