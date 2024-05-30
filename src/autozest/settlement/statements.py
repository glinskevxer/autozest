"""Buyer invoices and seller statements, built from the ledger.

Neither of these is stored. They are a view of the journal at a moment, which
matters because an arbitration decided on Thursday changes what Tuesday's
statement should have said, and a stored document would go on saying the wrong
thing until somebody remembered to rebuild it.

What that costs is that a statement handed to a seller is a snapshot and not a
record. The ledger is the record. A statement carries the moment it was built
for exactly that reason, so two copies that disagree can be told apart.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from autozest.block.sales import SaleRepository
from autozest.money import Money, total
from autozest.settlement.ledger import Account, Ledger
from autozest.storage.records import all_rows


class Side(Enum):
    BUYER = "buyer"
    SELLER = "seller"

    @property
    def account(self) -> Account:
        return Account.BUYER_RECEIVABLE if self is Side.BUYER else Account.SELLER_PAYABLE


@dataclass(frozen=True)
class StatementLine:
    """One line of a document, in the order it was posted."""

    reference: str
    memo: str
    amount: Money
    posted_at: datetime

    @property
    def is_credit(self) -> bool:
        return self.amount.is_negative


@dataclass(frozen=True)
class Statement:
    """What one party owes or is owed, as at a moment."""

    side: Side
    party_id: str
    lines: tuple[StatementLine, ...]
    built_at: datetime
    sale_code: str | None = None

    @property
    def balance(self) -> Money:
        return total([line.amount for line in self.lines])

    @property
    def charges(self) -> Money:
        return total([line.amount for line in self.lines if not line.is_credit])

    @property
    def credits(self) -> Money:
        return total([line.amount for line in self.lines if line.is_credit])

    @property
    def is_settled(self) -> bool:
        return self.balance == Money.zero()

    @property
    def owes_us(self) -> bool:
        """True when money is coming in rather than going out."""
        return self.side is Side.BUYER and self.balance.cents > 0


class StatementService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.ledger = Ledger(conn)
        self.sales = SaleRepository(conn)

    def build(self, side: Side, party_id: str, *, sale_code: str | None = None) -> Statement:
        """Every posted line against one party, newest last."""
        rows = all_rows(
            self.conn,
            "SELECT e.reference, e.memo, e.posted_at, "
            "l.debit_cents, l.credit_cents "
            "FROM ledger_line l JOIN ledger_entry e ON e.id = l.entry_id "
            "WHERE l.account = ? AND l.party_id = ? "
            "AND (? IS NULL OR e.reference LIKE ?) "
            "ORDER BY e.id, l.id",
            (
                side.account.value,
                party_id,
                sale_code,
                f"{sale_code}%" if sale_code else None,
            ),
        )
        lines = tuple(
            StatementLine(
                reference=row["reference"],
                memo=row["memo"],
                amount=self._signed(side, row["debit_cents"], row["credit_cents"]),
                posted_at=datetime.fromisoformat(row["posted_at"]),
            )
            for row in rows
        )
        return Statement(
            side=side,
            party_id=party_id,
            lines=lines,
            built_at=datetime.now(UTC),
            sale_code=sale_code,
        )

    @staticmethod
    def _signed(side: Side, debit_cents: int, credit_cents: int) -> Money:
        """Positive is what the party owes us, or what we owe them.

        A buyer receivable runs as a debit and a seller payable runs as a
        credit, so the two sides are flipped relative to each other. Flipping
        here rather than in the reader means a statement reads the same way for
        both, which is what everyone expects from a document.
        """
        if side is Side.BUYER:
            return Money(debit_cents - credit_cents)
        return Money(credit_cents - debit_cents)

    def invoice(self, buyer_id: str, sale_code: str | None = None) -> Statement:
        return self.build(Side.BUYER, buyer_id, sale_code=sale_code)

    def seller_statement(self, seller_id: str, sale_code: str | None = None) -> Statement:
        return self.build(Side.SELLER, seller_id, sale_code=sale_code)

    def outstanding_buyers(self) -> dict[str, Money]:
        """Every buyer with a balance, biggest first."""
        return self._balances(Side.BUYER)

    def unpaid_sellers(self) -> dict[str, Money]:
        return self._balances(Side.SELLER)

    def _balances(self, side: Side) -> dict[str, Money]:
        rows = all_rows(
            self.conn,
            "SELECT party_id, COALESCE(SUM(debit_cents), 0) AS debits, "
            "COALESCE(SUM(credit_cents), 0) AS credits FROM ledger_line "
            "WHERE account = ? AND party_id IS NOT NULL GROUP BY party_id",
            (side.account.value,),
        )
        balances = {
            row["party_id"]: self._signed(side, row["debits"], row["credits"]) for row in rows
        }
        return {
            party: amount
            for party, amount in sorted(
                balances.items(), key=lambda pair: pair[1].cents, reverse=True
            )
            if amount.cents != 0
        }

    def sale_totals(self, sale_code: str) -> dict[str, Money]:
        """What one sale did, for the office board."""
        self.sales.get(sale_code)
        rows = all_rows(
            self.conn,
            "SELECT l.account, COALESCE(SUM(l.debit_cents), 0) - "
            "COALESCE(SUM(l.credit_cents), 0) AS signed "
            "FROM ledger_line l JOIN ledger_entry e ON e.id = l.entry_id "
            "WHERE e.reference LIKE ? GROUP BY l.account",
            (f"{sale_code}%",),
        )
        return {row["account"]: Money(row["signed"]) for row in rows}
