"""The invoice and the statement, as documents rather than as balances.

A statement is not a list of ledger lines with a total. It is a piece of paper
somebody pays from, so it has the things a person needs to pay from it: who it
is for, what it is about, what to send and where to send it, and a reference to
quote so the money can be matched when it arrives.

That last one is the whole reason this exists. Half the bank lines that could
not be matched were wires with no reference on them, and every one of those was
sent by somebody working from a document that never gave them one to quote.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from autozest.catalog.accounts import AccountRepository
from autozest.config import Settings
from autozest.money import Money
from autozest.reporting.text import Align, Column, Table, key_values, wrap
from autozest.settlement.payments import BUYER_TERMS, PaymentService
from autozest.settlement.statements import Side, Statement, StatementService
from autozest.timing import BusinessCalendar

WIDTH = 88

LINE_TABLE = Table(
    [
        Column("reference", 20),
        Column("what", 40),
        Column("amount", 13, Align.RIGHT),
    ]
)


@dataclass(frozen=True)
class Document:
    """One rendered statement, and the reference to quote against it."""

    lines: tuple[str, ...]
    reference: str
    balance: Money
    due_on: date | None = None

    def text(self) -> str:
        return "\n".join(self.lines)


def payment_reference(side: Side, party_id: str, on: date) -> str:
    """What somebody paying should put in the reference field.

    Short, upper case, and carrying the account and the date, so an unmatched
    wire can still be traced by eye.
    """
    return f"{side.value[0].upper()}{party_id.upper().replace('-', '')}-{on:%y%m%d}"


def build(
    conn: sqlite3.Connection,
    side: Side,
    party_id: str,
    *,
    sale_code: str | None = None,
    settings: Settings | None = None,
    calendar: BusinessCalendar | None = None,
    on: date | None = None,
) -> Document:
    """Render a statement as the document it is."""
    config = settings or Settings()
    today = on or date.today()
    statement = StatementService(conn).build(side, party_id, sale_code=sale_code)
    party = AccountRepository(conn).get(party_id)
    reference = payment_reference(side, party_id, today)
    due = _due(conn, today, calendar) if side is Side.BUYER else None

    lines: list[str] = []
    title = "INVOICE" if side is Side.BUYER else "SELLER STATEMENT"
    lines.append(f"{config.auction_name}    {title}")
    lines.append("=" * WIDTH)
    lines.extend(
        key_values(
            [
                ("account", f"{party.name} ({party.id})"),
                ("sale", sale_code or "all sales"),
                ("as at", statement.built_at.strftime("%Y-%m-%d %H:%M")),
                ("reference", reference),
            ]
        )
    )
    lines.append("")
    rows: list[list[object]] = [
        [line.reference, line.memo, line.amount] for line in statement.lines
    ]
    lines.extend(LINE_TABLE.render(rows))
    lines.append("")
    lines.extend(
        key_values(
            [
                ("charges", statement.charges),
                ("credits", statement.credits),
                ("balance", statement.balance),
            ]
        )
    )
    lines.append("")
    lines.extend(_footer(side, statement, reference, due))
    return Document(
        lines=tuple(lines),
        reference=reference,
        balance=statement.balance,
        due_on=due,
    )


def _due(conn: sqlite3.Connection, today: date, calendar: BusinessCalendar | None) -> date:
    payments = PaymentService(conn, calendar)
    return payments.due_date(datetime.combine(today, datetime.min.time())).date()


def _footer(side: Side, statement: Statement, reference: str, due: date | None) -> list[str]:
    if statement.is_settled:
        return wrap("Nothing is outstanding on this account.", WIDTH)
    if side is Side.BUYER:
        return wrap(
            f"Please send {statement.balance} by {due:%d %B} quoting {reference}. "
            f"Terms are {BUYER_TERMS}. Titles on anything unpaid stay with us, and a "
            "late fee applies after the due date.",
            WIDTH,
        )
    return wrap(
        f"{statement.balance} is due to you. It is released once the buyer has paid, "
        "the title is with us and no arbitration is open on the unit. Quote "
        f"{reference} if you ring about it.",
        WIDTH,
    )
