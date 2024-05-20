"""A double entry journal.

Nothing here is edited and nothing is deleted. A mistake is corrected by
posting its reverse, which is what an auditor expects to find and what lets a
settlement clerk see what happened rather than what the current numbers imply.

Entries balance or they are refused. That is not a nicety: the one thing this
module has to guarantee is that the sum of everything owed to sellers, owed by
buyers and earned by the auction reconciles to the cash, and an unbalanced
entry breaks that quietly and permanently.

Accounts come in two kinds. A **party account** is meaningless without knowing
whose it is, so a receivable line carries the buyer. A **house account** like
fee income does not. Posting a party line without a party is refused, and so is
the other way round, because both of those turn up as a statement that does not
add up weeks later.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from autozest.errors import ValidationError
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, one, scalar


class Account(Enum):
    """The chart of accounts. Short, because a small auction needs no more."""

    CASH = "cash"
    BUYER_RECEIVABLE = "buyer_receivable"
    SELLER_PAYABLE = "seller_payable"
    PROCEEDS_HELD = "proceeds_held"
    FEE_INCOME = "fee_income"
    SERVICE_INCOME = "service_income"
    ARBITRATION_EXPENSE = "arbitration_expense"
    LIEN_CLEARING = "lien_clearing"
    TAX_PAYABLE = "tax_payable"
    WRITE_OFF = "write_off"

    @property
    def is_per_party(self) -> bool:
        """Whether a line on this account is meaningless without a party."""
        return self in _PER_PARTY

    @property
    def normal_side_is_debit(self) -> bool:
        """Which way this account normally runs, for presenting a balance."""
        return self in _DEBIT_NORMAL


_PER_PARTY = frozenset(
    {
        Account.BUYER_RECEIVABLE,
        Account.SELLER_PAYABLE,
        Account.PROCEEDS_HELD,
        Account.LIEN_CLEARING,
    }
)

_DEBIT_NORMAL = frozenset(
    {Account.CASH, Account.BUYER_RECEIVABLE, Account.ARBITRATION_EXPENSE, Account.WRITE_OFF}
)


@dataclass(frozen=True)
class Line:
    """One side of one movement."""

    account: Account
    debit: Money = Money.zero()
    credit: Money = Money.zero()
    party_id: str | None = None
    memo: str | None = None

    def __post_init__(self) -> None:
        if self.debit.is_negative or self.credit.is_negative:
            raise ValidationError("a ledger line is never negative, use the other side")
        if self.debit and self.credit:
            raise ValidationError("a ledger line is a debit or a credit, not both")
        if self.account.is_per_party and not self.party_id:
            raise ValidationError(
                f"{self.account.value} is kept per party and this line has none"
            )
        if not self.account.is_per_party and self.party_id:
            raise ValidationError(f"{self.account.value} is a house account and takes no party")

    @property
    def signed(self) -> Money:
        """Positive for a debit, negative for a credit."""
        return self.debit - self.credit


def debit(
    account: Account, amount: Money, party_id: str | None = None, memo: str | None = None
) -> Line:
    return Line(account=account, debit=amount, party_id=party_id, memo=memo)


def credit(
    account: Account, amount: Money, party_id: str | None = None, memo: str | None = None
) -> Line:
    return Line(account=account, credit=amount, party_id=party_id, memo=memo)


@dataclass(frozen=True)
class Entry:
    """One balanced movement."""

    reference: str
    memo: str
    source: str
    lines: tuple[Line, ...] = field(default_factory=tuple)
    source_id: str | None = None
    posted_at: datetime | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.lines:
            raise ValidationError("an entry with no lines is not an entry")
        if self.imbalance.cents != 0:
            raise ValidationError(
                f"the entry does not balance, debits are {self.imbalance} out"
            )

    @property
    def imbalance(self) -> Money:
        return Money(sum(line.signed.cents for line in self.lines))

    @property
    def total(self) -> Money:
        """The size of the movement, which is one side of it."""
        return Money(sum(line.debit.cents for line in self.lines))

    def reversed_as(self, reference: str, memo: str) -> Entry:
        """The same entry the other way round."""
        return Entry(
            reference=reference,
            memo=memo,
            source=self.source,
            source_id=self.source_id,
            lines=tuple(
                Line(
                    account=line.account,
                    debit=line.credit,
                    credit=line.debit,
                    party_id=line.party_id,
                    memo=line.memo,
                )
                for line in self.lines
            ),
        )


class Ledger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def post(self, entry: Entry) -> Entry:
        """Write a balanced entry. The constructor has already checked it."""
        insert(
            self.conn,
            "ledger_entry",
            {
                "reference": entry.reference,
                "memo": entry.memo,
                "source": entry.source,
                "source_id": entry.source_id,
                "posted_at": now_text(),
            },
        )
        entry_id = int(scalar(self.conn, "SELECT last_insert_rowid()") or 0)
        for line in entry.lines:
            insert(
                self.conn,
                "ledger_line",
                {
                    "entry_id": entry_id,
                    "account": line.account.value,
                    "party_id": line.party_id,
                    "debit_cents": line.debit.cents,
                    "credit_cents": line.credit.cents,
                    "memo": line.memo,
                },
            )
        return self.get(entry_id)

    def reverse(self, entry_id: int, reason: str) -> Entry:
        """Undo an entry by posting its mirror, never by deleting it."""
        original = self.get(entry_id)
        mirror = original.reversed_as(original.reference, f"reversal: {reason}")
        posted = self.post(mirror)
        assert posted.id is not None
        self.conn.execute(
            "UPDATE ledger_entry SET reverses = ? WHERE id = ?", (entry_id, posted.id)
        )
        return self.get(posted.id)

    def get(self, entry_id: int) -> Entry:
        row = one(self.conn, "SELECT * FROM ledger_entry WHERE id = ?", (entry_id,))
        if row is None:
            raise ValidationError(f"no ledger entry {entry_id}")
        lines = all_rows(
            self.conn,
            "SELECT account, party_id, debit_cents, credit_cents, memo FROM ledger_line "
            "WHERE entry_id = ? ORDER BY id",
            (entry_id,),
        )
        return Entry(
            id=row["id"],
            reference=row["reference"],
            memo=row["memo"],
            source=row["source"],
            source_id=row["source_id"],
            posted_at=datetime.fromisoformat(row["posted_at"]),
            lines=tuple(
                Line(
                    account=Account(line["account"]),
                    debit=Money(line["debit_cents"]),
                    credit=Money(line["credit_cents"]),
                    party_id=line["party_id"],
                    memo=line["memo"],
                )
                for line in lines
            ),
        )

    def balance(self, account: Account, party_id: str | None = None) -> Money:
        """The balance, signed the way the account normally runs."""
        row = one(
            self.conn,
            "SELECT COALESCE(SUM(debit_cents), 0) AS debits, "
            "COALESCE(SUM(credit_cents), 0) AS credits FROM ledger_line "
            "WHERE account = ? AND (? IS NULL OR party_id = ?)",
            (account.value, party_id, party_id),
        )
        assert row is not None
        signed = Money(row["debits"] - row["credits"])
        return signed if account.normal_side_is_debit else -signed

    def entries_for(self, source: str, source_id: str) -> list[Entry]:
        rows = all_rows(
            self.conn,
            "SELECT id FROM ledger_entry WHERE source = ? AND source_id = ? ORDER BY id",
            (source, source_id),
        )
        return [self.get(row["id"]) for row in rows]

    def trial_balance(self) -> dict[Account, Money]:
        """Every account with a balance, which must sum to nothing."""
        rows = all_rows(
            self.conn,
            "SELECT account, COALESCE(SUM(debit_cents), 0) - COALESCE(SUM(credit_cents), 0) "
            "AS signed FROM ledger_line GROUP BY account ORDER BY account",
        )
        return {Account(row["account"]): Money(row["signed"]) for row in rows}

    def is_balanced(self) -> bool:
        total = scalar(
            self.conn,
            "SELECT COALESCE(SUM(debit_cents), 0) - COALESCE(SUM(credit_cents), 0) "
            "FROM ledger_line",
        )
        return int(total or 0) == 0
