"""Who has owed us money for how long.

Aged by the date the charge was posted rather than the date of the sale. Those
are usually the same day and occasionally are not, and when they differ it is
because the charge went on late, which means the clock the buyer is fairly held
to started late as well.

Buckets are counted in business days, like everything else here. A buyer whose
wire is caught behind a bank holiday is not thirty days late, and putting them
in a bucket that says so is how a good customer gets a rude phone call.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from autozest.money import Money, total
from autozest.settlement.ledger import Account
from autozest.settlement.statements import Side
from autozest.storage.records import all_rows
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar

#: Upper bound of each bucket in business days. The last one is open ended.
BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("current", 2),
    ("3 to 7", 7),
    ("8 to 14", 14),
    ("15 to 30", 30),
    ("over 30", None),
)


@dataclass(frozen=True)
class AgedLine:
    """One posted charge and how old it is."""

    party_id: str
    reference: str
    memo: str
    amount: Money
    posted_at: datetime
    age_days: int

    @property
    def bucket(self) -> str:
        for name, upper in BUCKETS:
            if upper is None or self.age_days <= upper:
                return name
        return BUCKETS[-1][0]


@dataclass(frozen=True)
class AgedParty:
    """One party's balance, split across the buckets."""

    party_id: str
    lines: tuple[AgedLine, ...]

    @property
    def balance(self) -> Money:
        return total([line.amount for line in self.lines])

    def bucket(self, name: str) -> Money:
        return total([line.amount for line in self.lines if line.bucket == name])

    @property
    def oldest(self) -> int:
        return max((line.age_days for line in self.lines), default=0)

    @property
    def is_overdue(self) -> bool:
        return self.oldest > BUCKETS[0][1] if BUCKETS[0][1] is not None else False


@dataclass(frozen=True)
class Aging:
    """The whole aged list."""

    side: Side
    as_at: datetime
    parties: tuple[AgedParty, ...]

    @property
    def total(self) -> Money:
        return total([party.balance for party in self.parties])

    def bucket(self, name: str) -> Money:
        return total([party.bucket(name) for party in self.parties])

    @property
    def overdue(self) -> tuple[AgedParty, ...]:
        return tuple(party for party in self.parties if party.is_overdue)

    def totals(self) -> dict[str, Money]:
        return {name: self.bucket(name) for name, _ in BUCKETS}


def build(
    conn: sqlite3.Connection,
    side: Side = Side.BUYER,
    *,
    as_at: datetime | None = None,
    calendar: BusinessCalendar | None = None,
) -> Aging:
    """Age everything posted against one side of the book."""
    moment = as_at or datetime.now().astimezone()
    business = calendar or DEFAULT_CALENDAR
    rows = all_rows(
        conn,
        "SELECT l.party_id, e.reference, e.memo, e.posted_at, "
        "l.debit_cents, l.credit_cents "
        "FROM ledger_line l JOIN ledger_entry e ON e.id = l.entry_id "
        "WHERE l.account = ? AND l.party_id IS NOT NULL ORDER BY l.party_id, e.id",
        (side.account.value,),
    )
    by_party: dict[str, list[AgedLine]] = {}
    for row in rows:
        posted = datetime.fromisoformat(row["posted_at"])
        amount = _signed(side, row["debit_cents"], row["credit_cents"])
        by_party.setdefault(row["party_id"], []).append(
            AgedLine(
                party_id=row["party_id"],
                reference=row["reference"],
                memo=row["memo"],
                amount=amount,
                posted_at=posted,
                age_days=_age(business, posted, moment),
            )
        )
    parties = tuple(
        AgedParty(party_id=party, lines=tuple(lines))
        for party, lines in sorted(by_party.items())
        if total([line.amount for line in lines]).cents != 0
    )
    return Aging(side=side, as_at=moment, parties=parties)


def _age(calendar: BusinessCalendar, posted: datetime, now: datetime) -> int:
    if now < posted:
        return 0
    return calendar.business_days_between(posted.date(), now.date())


def _signed(side: Side, debit_cents: int, credit_cents: int) -> Money:
    if side is Side.BUYER:
        return Money(debit_cents - credit_cents)
    return Money(credit_cents - debit_cents)


def bucket_names() -> Sequence[str]:
    return [name for name, _ in BUCKETS]


def account_for(side: Side) -> Account:
    return side.account
