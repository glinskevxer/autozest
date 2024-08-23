"""Floor plan credit.

Most dealers do not buy stock with their own money. A finance company gives
them a line, the auction draws on it when the hammer falls, and the dealer
repays the lender when the unit retails off their forecourt.

The consequence for this system is awkward and worth stating: **whether a buyer
may bid is a question about somebody else's balance sheet**. It has to be
answered before the hammer rather than after, because a sale the lender will
not fund is not a sale, it is a unit sitting in the yard and two people
arguing.

Capacity is the limit less what is drawn and still outstanding, less what the
buyer already owes us on account. That second part surprises people. A dealer
who has bought forty thousand dollars of stock this morning and not paid for it
has that much less room, whether or not any of it has been drawn yet, because
the auction is carrying the exposure either way.

Where a group shares credit, the whole pool is one number. Four accounts under
one group with a shared line have one limit between them, not four, and
computing it per account is how a group quietly runs at four times the exposure
their lender agreed to.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.catalog.groups import GroupService
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from autozest.money import Money, total
from autozest.settlement.statements import StatementService
from autozest.storage.records import all_rows, insert, one, update


class LineStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"

    @property
    def can_draw(self) -> bool:
        return self is LineStatus.ACTIVE


@dataclass(frozen=True)
class CreditLine:
    """One lender's line for one buyer."""

    id: str
    buyer_id: str
    lender: str
    limit: Money
    status: LineStatus = LineStatus.ACTIVE
    opened_on: date | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.limit.is_negative:
            raise ValidationError("a credit limit cannot be negative")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CreditLine:
        return cls(
            id=row["id"],
            buyer_id=row["buyer_id"],
            lender=row["lender"],
            limit=Money(row["limit_cents"]),
            status=LineStatus(row["status"]),
            opened_on=date.fromisoformat(row["opened_on"]),
            note=row["note"],
        )


@dataclass(frozen=True)
class Draw:
    """One drawing against a line."""

    id: int
    line_id: str
    amount: Money
    drawn_at: datetime
    run_id: int | None = None
    repaid_at: datetime | None = None
    note: str | None = None

    @property
    def is_outstanding(self) -> bool:
        return self.repaid_at is None


@dataclass(frozen=True)
class Capacity:
    """What a buyer can still spend, and why it is that number."""

    buyer_id: str
    limit: Money
    drawn: Money
    on_account: Money
    pooled_with: tuple[str, ...] = ()

    @property
    def is_pooled(self) -> bool:
        return bool(self.pooled_with)

    @property
    def available(self) -> Money:
        room = self.limit - self.drawn - self.on_account
        return room if room.cents > 0 else Money.zero()

    @property
    def is_exhausted(self) -> bool:
        return self.available == Money.zero()

    def explain(self) -> str:
        text = (
            f"{self.available} of {self.limit}, "
            f"{self.drawn} drawn and {self.on_account} owed on account"
        )
        if self.pooled_with:
            text += f", shared with {', '.join(self.pooled_with)}"
        return text


class FloorplanService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.statements = StatementService(conn)
        self.groups = GroupService(conn)

    def open_line(
        self,
        line_id: str,
        buyer_id: str,
        lender: str,
        limit: Money,
        *,
        on: date | None = None,
        note: str | None = None,
    ) -> CreditLine:
        line = CreditLine(id=line_id, buyer_id=buyer_id, lender=lender, limit=limit, note=note)
        insert(
            self.conn,
            "credit_line",
            {
                "id": line.id,
                "buyer_id": line.buyer_id,
                "lender": line.lender,
                "limit_cents": line.limit.cents,
                "status": line.status.value,
                "opened_on": (on or date.today()).isoformat(),
                "note": line.note,
            },
        )
        return self.line(line_id)

    def line(self, line_id: str) -> CreditLine:
        row = one(self.conn, "SELECT * FROM credit_line WHERE id = ?", (line_id,))
        if row is None:
            raise NotFound("credit line", line_id)
        return CreditLine.from_row(row)

    def lines_for(self, buyer_id: str) -> list[CreditLine]:
        rows = all_rows(
            self.conn, "SELECT * FROM credit_line WHERE buyer_id = ? ORDER BY id", (buyer_id,)
        )
        return [CreditLine.from_row(row) for row in rows]

    def set_status(self, line_id: str, status: LineStatus) -> CreditLine:
        self.line(line_id)
        update(self.conn, "credit_line", {"id": line_id}, {"status": status.value})
        return self.line(line_id)

    def set_limit(self, line_id: str, limit: Money) -> CreditLine:
        if limit.is_negative:
            raise ValidationError("a credit limit cannot be negative")
        self.line(line_id)
        update(self.conn, "credit_line", {"id": line_id}, {"limit_cents": limit.cents})
        return self.line(line_id)

    def capacity(self, buyer_id: str) -> Capacity:
        """What is left, across whatever accounts share the line.

        A group that shares credit has one limit between all of its accounts.
        Reading it per account gives a group with four numbers four times the
        room their lender agreed to.
        """
        pool = self.groups.credit_pool(buyer_id)
        lines = [
            line
            for account_id in pool
            for line in self.lines_for(account_id)
            if line.status.can_draw
        ]
        limit = total([line.limit for line in lines])
        drawn = total(
            [
                draw.amount
                for line in lines
                for draw in self.draws(line.id)
                if draw.is_outstanding
            ]
        )
        owed = total([self.statements.invoice(account_id).balance for account_id in pool])
        return Capacity(
            buyer_id=buyer_id,
            limit=limit,
            drawn=drawn,
            on_account=owed if owed.cents > 0 else Money.zero(),
            pooled_with=tuple(account for account in pool if account != buyer_id),
        )

    def require_capacity(self, buyer_id: str, amount: Money) -> Capacity:
        """Raise unless the buyer can carry this much more."""
        capacity = self.capacity(buyer_id)
        if capacity.limit == Money.zero():
            return capacity
        if amount > capacity.available:
            raise PolicyViolation(
                "credit.exhausted",
                f"that would put {buyer_id} past their line, {capacity.explain()}",
                buyer=buyer_id,
                wanted=str(amount),
                available=str(capacity.available),
            )
        return capacity

    def draw(
        self,
        line_id: str,
        amount: Money,
        *,
        run_id: int | None = None,
        at: datetime | None = None,
        note: str | None = None,
    ) -> Draw:
        """Draw against a line."""
        line = self.line(line_id)
        if not line.status.can_draw:
            raise PolicyViolation(
                "credit.line_closed",
                f"that line is {line.status.value}",
                line=line_id,
            )
        if amount.cents <= 0:
            raise ValidationError("a drawing has to be for something")
        if run_id is not None and self.draw_for_run(run_id) is not None:
            raise Conflict("that run has already been drawn", run=run_id)
        insert(
            self.conn,
            "credit_draw",
            {
                "line_id": line_id,
                "run_id": run_id,
                "amount_cents": amount.cents,
                "drawn_at": (at or datetime.now().astimezone()).isoformat(timespec="seconds"),
                "note": note,
            },
        )
        drawn = self.draws(line_id)
        return drawn[-1]

    def repay(self, draw_id: int, *, at: datetime | None = None) -> None:
        update(
            self.conn,
            "credit_draw",
            {"id": draw_id},
            {"repaid_at": (at or datetime.now().astimezone()).isoformat(timespec="seconds")},
        )

    def draws(self, line_id: str) -> list[Draw]:
        rows = all_rows(
            self.conn, "SELECT * FROM credit_draw WHERE line_id = ? ORDER BY id", (line_id,)
        )
        return [self._draw(row) for row in rows]

    def draw_for_run(self, run_id: int) -> Draw | None:
        row = one(self.conn, "SELECT * FROM credit_draw WHERE run_id = ?", (run_id,))
        return self._draw(row) if row else None

    @staticmethod
    def _draw(row: dict[str, Any]) -> Draw:
        return Draw(
            id=row["id"],
            line_id=row["line_id"],
            run_id=row["run_id"],
            amount=Money(row["amount_cents"]),
            drawn_at=datetime.fromisoformat(row["drawn_at"]),
            repaid_at=(datetime.fromisoformat(row["repaid_at"]) if row["repaid_at"] else None),
            note=row["note"],
        )

    def outstanding_for(self, buyer_id: str) -> Money:
        return total(
            [
                draw.amount
                for line in self.lines_for(buyer_id)
                for draw in self.draws(line.id)
                if draw.is_outstanding
            ]
        )
