"""Money actually moving.

A payment is not the same thing as a balance. The ledger knows what is owed;
this knows that a cheque numbered 40182 arrived on the fourteenth, which is
what somebody needs when the bank says it did not.

Payments run both ways and the two are not symmetric. A buyer pays us and the
cash comes in against their receivable. We pay a seller and the cash goes out
against their payable, and that one is only allowed when nothing is holding the
proceeds, which is a question this module asks rather than answers.

Terms are counted in business days from the sale, so a buyer who takes delivery
on the Friday of a holiday weekend is not late on the Tuesday.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from autozest.errors import Conflict, PolicyViolation, ValidationError
from autozest.identifiers import sequenced
from autozest.money import Money
from autozest.settlement.ledger import Account, Entry, Ledger, credit, debit
from autozest.settlement.statements import Side, StatementService
from autozest.storage.records import all_rows, insert, one, scalar
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar, Window, WindowUnit

PAYMENT_PREFIX = "M"

#: How long a buyer has to pay before a late fee is due.
BUYER_TERMS = Window(2, WindowUnit.BUSINESS_DAYS)


class Method(Enum):
    CASH = "cash"
    CHEQUE = "cheque"
    WIRE = "wire"
    CARD = "card"
    FLOOR_PLAN = "floor_plan"
    OFFSET = "offset"

    @property
    def clears_immediately(self) -> bool:
        """Whether a title can go out on the strength of it the same day."""
        return self in (Method.CASH, Method.WIRE, Method.FLOOR_PLAN, Method.OFFSET)


@dataclass(frozen=True)
class Payment:
    """One movement of cash."""

    id: str
    party_id: str
    side: Side
    amount: Money
    method: Method
    received_at: datetime
    reference: str | None = None
    sale_code: str | None = None
    note: str | None = None


class PaymentService:
    def __init__(
        self, conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
    ) -> None:
        self.conn = conn
        self.ledger = Ledger(conn)
        self.statements = StatementService(conn)
        self.calendar = calendar or DEFAULT_CALENDAR

    def next_id(self, when: date | None = None) -> str:
        day = when or date.today()
        used = scalar(
            self.conn,
            "SELECT COUNT(*) FROM payment WHERE id LIKE ?",
            (f"{PAYMENT_PREFIX}-{day.year}-%",),
        )
        return sequenced(PAYMENT_PREFIX, day, int(used or 0) + 1)

    def receive(
        self,
        buyer_id: str,
        amount: Money,
        *,
        method: Method = Method.WIRE,
        reference: str | None = None,
        sale_code: str | None = None,
        at: datetime | None = None,
        note: str | None = None,
    ) -> Payment:
        """Take money in from a buyer."""
        self._require_positive(amount)
        when = at or datetime.now().astimezone()
        entry = self.ledger.post(
            Entry(
                reference=sale_code or "payment",
                memo=f"{method.value} from {buyer_id}",
                source="payment",
                source_id=reference,
                lines=(
                    debit(Account.CASH, amount),
                    credit(Account.BUYER_RECEIVABLE, amount, buyer_id),
                ),
            )
        )
        return self._record(
            buyer_id, Side.BUYER, amount, method, when, reference, sale_code, note, entry
        )

    def pay_out(
        self,
        seller_id: str,
        amount: Money,
        *,
        method: Method = Method.CHEQUE,
        reference: str | None = None,
        sale_code: str | None = None,
        at: datetime | None = None,
        note: str | None = None,
        holds: list[str] | None = None,
    ) -> Payment:
        """Pay a seller, if nothing is holding the proceeds."""
        self._require_positive(amount)
        if holds:
            raise PolicyViolation(
                "payment.held",
                "; ".join(holds),
                seller=seller_id,
                holds=len(holds),
            )
        owed = self.statements.seller_statement(seller_id).balance
        if amount > owed:
            raise PolicyViolation(
                "payment.overpay",
                "that is more than the seller is owed",
                seller=seller_id,
                owed=str(owed),
                paying=str(amount),
            )
        when = at or datetime.now().astimezone()
        entry = self.ledger.post(
            Entry(
                reference=sale_code or "payout",
                memo=f"{method.value} to {seller_id}",
                source="payment",
                source_id=reference,
                lines=(
                    debit(Account.SELLER_PAYABLE, amount, seller_id),
                    credit(Account.CASH, amount),
                ),
            )
        )
        return self._record(
            seller_id, Side.SELLER, amount, method, when, reference, sale_code, note, entry
        )

    def _record(
        self,
        party_id: str,
        side: Side,
        amount: Money,
        method: Method,
        when: datetime,
        reference: str | None,
        sale_code: str | None,
        note: str | None,
        entry: Entry,
    ) -> Payment:
        payment_id = self.next_id(when.date())
        insert(
            self.conn,
            "payment",
            {
                "id": payment_id,
                "party_id": party_id,
                "side": side.value,
                "amount_cents": amount.cents,
                "method": method.value,
                "reference": reference,
                "sale_code": sale_code,
                "entry_id": entry.id,
                "received_at": when.isoformat(timespec="seconds"),
                "note": note,
            },
        )
        return self.get(payment_id)

    def get(self, payment_id: str) -> Payment:
        row = one(self.conn, "SELECT * FROM payment WHERE id = ?", (payment_id,))
        if row is None:
            raise Conflict(f"no payment {payment_id}")
        return Payment(
            id=row["id"],
            party_id=row["party_id"],
            side=Side(row["side"]),
            amount=Money(row["amount_cents"]),
            method=Method(row["method"]),
            received_at=datetime.fromisoformat(row["received_at"]),
            reference=row["reference"],
            sale_code=row["sale_code"],
            note=row["note"],
        )

    def for_party(self, party_id: str) -> list[Payment]:
        rows = all_rows(
            self.conn, "SELECT id FROM payment WHERE party_id = ? ORDER BY id", (party_id,)
        )
        return [self.get(row["id"]) for row in rows]

    def due_date(self, sold_at: datetime) -> datetime:
        """When a buyer's money is late."""
        return self.calendar.deadline(sold_at, BUYER_TERMS)

    def is_late(self, sold_at: datetime, now: datetime) -> bool:
        return now > self.due_date(sold_at)

    def has_paid(self, buyer_id: str, sale_code: str | None = None) -> bool:
        return self.statements.invoice(buyer_id, sale_code).balance.cents <= 0

    @staticmethod
    def _require_positive(amount: Money) -> None:
        if amount.cents <= 0:
            raise ValidationError("a payment has to be for something")
