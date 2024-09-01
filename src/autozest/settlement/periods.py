"""Accounting periods, and closing them.

A closed period takes no more entries. That sounds obvious and is the most
argued about rule in the building, because somebody always wants to put one
more thing into last month. The answer is a correcting entry in this month,
which is what an auditor expects to find anyway and what the reversal machinery
in the ledger already does.

Closing is two steps rather than one. A period goes to closing first, which
stops new entries while the checks run, and only becomes closed when everything
that has to balance balances. A single step close either lets entries in during
the checks or blocks the building while somebody investigates a difference.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.errors import Conflict, NotFound, PolicyViolation, StateError, ValidationError
from autozest.money import Money
from autozest.settlement.ledger import Ledger
from autozest.storage.records import all_rows, insert, one, scalar, update


class PeriodStatus(Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"

    @property
    def accepts_entries(self) -> bool:
        return self is PeriodStatus.OPEN


TRANSITIONS: dict[PeriodStatus, frozenset[PeriodStatus]] = {
    PeriodStatus.OPEN: frozenset({PeriodStatus.CLOSING}),
    PeriodStatus.CLOSING: frozenset({PeriodStatus.OPEN, PeriodStatus.CLOSED}),
    PeriodStatus.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class Period:
    """One accounting month."""

    id: str
    starts_on: date
    ends_on: date
    status: PeriodStatus = PeriodStatus.OPEN
    closed_by: str | None = None
    closed_at: datetime | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.ends_on < self.starts_on:
            raise ValidationError("a period cannot end before it starts")

    def covers(self, day: date) -> bool:
        return self.starts_on <= day <= self.ends_on

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Period:
        return cls(
            id=row["id"],
            starts_on=date.fromisoformat(row["starts_on"]),
            ends_on=date.fromisoformat(row["ends_on"]),
            status=PeriodStatus(row["status"]),
            closed_by=row["closed_by"],
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            note=row["note"],
        )


@dataclass(frozen=True)
class CloseCheck:
    """What has to be true before a period can close."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        mark = "ok  " if self.passed else "FAIL"
        return f"{mark} {self.name}: {self.detail}"


class PeriodService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.ledger = Ledger(conn)

    def open_period(self, starts_on: date, ends_on: date) -> Period:
        period = Period(id=f"P-{starts_on:%Y%m}", starts_on=starts_on, ends_on=ends_on)
        if self.covering(starts_on) is not None:
            raise Conflict("a period already covers that date", starts_on=starts_on.isoformat())
        insert(
            self.conn,
            "accounting_period",
            {
                "id": period.id,
                "starts_on": starts_on.isoformat(),
                "ends_on": ends_on.isoformat(),
                "status": period.status.value,
            },
        )
        return self.get(period.id)

    def get(self, period_id: str) -> Period:
        row = one(self.conn, "SELECT * FROM accounting_period WHERE id = ?", (period_id,))
        if row is None:
            raise NotFound("accounting period", period_id)
        return Period.from_row(row)

    def covering(self, day: date) -> Period | None:
        row = one(
            self.conn,
            "SELECT * FROM accounting_period WHERE starts_on <= ? AND ends_on >= ?",
            (day.isoformat(), day.isoformat()),
        )
        return Period.from_row(row) if row else None

    def periods(self) -> list[Period]:
        rows = all_rows(self.conn, "SELECT * FROM accounting_period ORDER BY starts_on")
        return [Period.from_row(row) for row in rows]

    def require_open(self, day: date) -> None:
        """Raise unless something may be posted on this date.

        A date no period covers is fine. Periods are opened as the year goes on
        and refusing to post because nobody has created next month yet would
        stop the building for an administrative reason.
        """
        period = self.covering(day)
        if period is not None and not period.status.accepts_entries:
            raise PolicyViolation(
                "period.closed",
                f"{period.id} is {period.status.value}, post the correction in the "
                "period that is open",
                period=period.id,
                day=day.isoformat(),
            )

    def checks(self, period_id: str) -> list[CloseCheck]:
        """Everything that has to be true before this period closes."""
        period = self.get(period_id)
        found: list[CloseCheck] = []
        found.append(
            CloseCheck(
                "ledger balances",
                self.ledger.is_balanced(),
                "debits equal credits across the whole journal",
            )
        )
        unposted = self._unposted_runs(period)
        found.append(
            CloseCheck(
                "sold runs posted",
                unposted == 0,
                f"{unposted} sold runs have no ledger entry",
            )
        )
        open_claims = int(
            scalar(
                self.conn,
                "SELECT COUNT(*) FROM arbitration_claim "
                "WHERE status IN ('open', 'under_review')",
            )
            or 0
        )
        found.append(
            CloseCheck(
                "arbitration settled",
                open_claims == 0,
                f"{open_claims} claims are still open",
            )
        )
        return found

    def _unposted_runs(self, period: Period) -> int:
        return int(
            scalar(
                self.conn,
                "SELECT COUNT(*) FROM run r JOIN sale s ON s.code = r.sale_code "
                "WHERE r.status = 'sold' AND s.sale_date BETWEEN ? AND ? "
                "AND NOT EXISTS (SELECT 1 FROM ledger_entry e "
                "WHERE e.source = 'run' AND e.source_id = CAST(r.id AS TEXT))",
                (period.starts_on.isoformat(), period.ends_on.isoformat()),
            )
            or 0
        )

    def begin_close(self, period_id: str) -> Period:
        """Stop new entries while the checks run."""
        return self._move(period_id, PeriodStatus.CLOSING)

    def reopen(self, period_id: str) -> Period:
        """Put a period that failed its checks back into service."""
        return self._move(period_id, PeriodStatus.OPEN)

    def close(self, period_id: str, *, closed_by: str, note: str | None = None) -> Period:
        """Close for good, if every check passes."""
        failures = [check for check in self.checks(period_id) if not check.passed]
        if failures:
            raise PolicyViolation(
                "period.checks_failed",
                "; ".join(check.detail for check in failures),
                period=period_id,
                failed=len(failures),
            )
        period = self._move(period_id, PeriodStatus.CLOSED)
        update(
            self.conn,
            "accounting_period",
            {"id": period.id},
            {
                "closed_by": closed_by,
                "closed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "note": note,
            },
        )
        return self.get(period_id)

    def _move(self, period_id: str, status: PeriodStatus) -> Period:
        period = self.get(period_id)
        if status not in TRANSITIONS[period.status]:
            allowed = (
                ", ".join(sorted(s.value for s in TRANSITIONS[period.status])) or "nothing"
            )
            raise StateError(f"period {period_id}", period.status.value, allowed)
        update(self.conn, "accounting_period", {"id": period_id}, {"status": status.value})
        return self.get(period_id)

    def totals(self, period_id: str) -> dict[str, Money]:
        """What the period did, by account."""
        period = self.get(period_id)
        rows = all_rows(
            self.conn,
            "SELECT l.account, COALESCE(SUM(l.debit_cents), 0) - "
            "COALESCE(SUM(l.credit_cents), 0) AS signed "
            "FROM ledger_line l JOIN ledger_entry e ON e.id = l.entry_id "
            "WHERE date(e.posted_at) BETWEEN ? AND ? GROUP BY l.account ORDER BY l.account",
            (period.starts_on.isoformat(), period.ends_on.isoformat()),
        )
        return {row["account"]: Money(row["signed"]) for row in rows}
