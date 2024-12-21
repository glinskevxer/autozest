"""Write offs and credit notes.

Two things that look the same on a balance and are not.

A **credit note** is money the auction agreed to give back. A fee charged in
error, a goodwill gesture on a unit that took six weeks to title. It is a
decision somebody made and it has a reason attached.

A **write off** is money the auction has given up on collecting. Nobody agreed
to anything; a buyer stopped answering the phone. It says something about the
account and it should follow the account around, which is why it is a different
posting and a different report.

Both need an approver above a threshold, and the threshold is deliberately low.
The point is not to stop anybody, it is that a second name on a hundred pound
write off costs nothing and a second name on a habit costs a great deal less
than the habit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from autozest.errors import PolicyViolation, ValidationError
from autozest.identifiers import sequenced
from autozest.money import Money, total
from autozest.settlement.ledger import Account, Entry, Ledger, credit, debit
from autozest.settlement.statements import Side, StatementService
from autozest.storage.records import all_rows, scalar

ADJUSTMENT_PREFIX = "J"

#: Above this an adjustment needs a second name.
APPROVAL_THRESHOLD = Money.parse("250.00")


class AdjustmentKind(Enum):
    CREDIT_NOTE = "credit_note"
    WRITE_OFF = "write_off"

    @property
    def is_agreed(self) -> bool:
        """Whether somebody decided this rather than gave up on it."""
        return self is AdjustmentKind.CREDIT_NOTE


@dataclass(frozen=True)
class Adjustment:
    """One movement that is not a sale, a fee or a payment."""

    reference: str
    kind: AdjustmentKind
    party_id: str
    side: Side
    amount: Money
    reason: str
    raised_by: str
    approved_by: str | None = None
    made_at: datetime | None = None

    @property
    def needed_approval(self) -> bool:
        return self.amount > APPROVAL_THRESHOLD


class AdjustmentService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.ledger = Ledger(conn)
        self.statements = StatementService(conn)

    def _next(self) -> int:
        used = scalar(
            self.conn, "SELECT COUNT(*) FROM ledger_entry WHERE source = 'adjustment'"
        )
        return int(used or 0) + 1

    def raise_adjustment(
        self,
        kind: AdjustmentKind,
        party_id: str,
        side: Side,
        amount: Money,
        *,
        reason: str,
        raised_by: str,
        approved_by: str | None = None,
    ) -> Adjustment:
        """Post a credit note or a write off against a party."""
        if amount.cents <= 0:
            raise ValidationError("an adjustment has to be for something")
        if not reason.strip():
            raise ValidationError("an adjustment needs a reason on the record")
        adjustment = Adjustment(
            reference=sequenced(ADJUSTMENT_PREFIX, date.today(), self._next()),
            kind=kind,
            party_id=party_id,
            side=side,
            amount=amount,
            reason=reason,
            raised_by=raised_by,
            approved_by=approved_by,
        )
        if adjustment.needed_approval and not approved_by:
            raise PolicyViolation(
                "adjustment.needs_approval",
                f"anything over {APPROVAL_THRESHOLD} needs a second name",
                party=party_id,
                amount=str(amount),
            )
        if approved_by and approved_by == raised_by:
            raise PolicyViolation(
                "adjustment.same_person",
                "the second name has to be a different person",
                party=party_id,
            )
        self.ledger.post(self._entry(adjustment))
        return adjustment

    def _entry(self, adjustment: Adjustment) -> Entry:
        """Where the money goes depends on which side it was owed on."""
        contra = (
            Account.WRITE_OFF
            if adjustment.kind is AdjustmentKind.WRITE_OFF
            else Account.FEE_INCOME
        )
        if adjustment.side is Side.BUYER:
            lines = (
                debit(contra, adjustment.amount),
                credit(Account.BUYER_RECEIVABLE, adjustment.amount, adjustment.party_id),
            )
        else:
            lines = (
                debit(contra, adjustment.amount),
                credit(Account.SELLER_PAYABLE, adjustment.amount, adjustment.party_id),
            )
        return Entry(
            reference=adjustment.reference,
            memo=f"{adjustment.kind.value}: {adjustment.reason}",
            source="adjustment",
            source_id=adjustment.party_id,
            lines=lines,
        )

    def credit_note(
        self,
        party_id: str,
        amount: Money,
        *,
        reason: str,
        raised_by: str,
        approved_by: str | None = None,
        side: Side = Side.BUYER,
    ) -> Adjustment:
        return self.raise_adjustment(
            AdjustmentKind.CREDIT_NOTE,
            party_id,
            side,
            amount,
            reason=reason,
            raised_by=raised_by,
            approved_by=approved_by,
        )

    def write_off(
        self,
        party_id: str,
        amount: Money,
        *,
        reason: str,
        raised_by: str,
        approved_by: str | None = None,
    ) -> Adjustment:
        owed = self.statements.invoice(party_id).balance
        if amount > owed:
            raise PolicyViolation(
                "adjustment.over_balance",
                f"{party_id} only owes {owed}",
                party=party_id,
                amount=str(amount),
            )
        return self.raise_adjustment(
            AdjustmentKind.WRITE_OFF,
            party_id,
            Side.BUYER,
            amount,
            reason=reason,
            raised_by=raised_by,
            approved_by=approved_by,
        )

    def history(self, party_id: str) -> list[tuple[str, Money, str]]:
        """Every adjustment against a party, for the account screen."""
        rows = all_rows(
            self.conn,
            "SELECT e.reference, e.memo, l.credit_cents FROM ledger_entry e "
            "JOIN ledger_line l ON l.entry_id = e.id "
            "WHERE e.source = 'adjustment' AND e.source_id = ? AND l.party_id = ? "
            "ORDER BY e.id",
            (party_id, party_id),
        )
        return [(row["reference"], Money(row["credit_cents"]), row["memo"]) for row in rows]

    def written_off(self) -> Money:
        """Everything given up on, which is the number the board asks for."""
        rows = all_rows(
            self.conn,
            "SELECT debit_cents FROM ledger_line WHERE account = ?",
            (Account.WRITE_OFF.value,),
        )
        return total([Money(row["debit_cents"]) for row in rows])
