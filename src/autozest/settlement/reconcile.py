"""Matching the bank against the ledger.

The bank sends a CSV every morning. Most of it matches on the reference,
because a dealer sending a wire types the invoice number into the reference
field. A useful fraction does not, because they typed their own name, or the
invoice number of the sale before, or nothing at all.

So matching runs in three passes, hardest evidence first, and stops at the
first pass that gives exactly one candidate:

1. the reference matches a payment we already recorded
2. the amount and the date match a buyer's outstanding balance exactly
3. the amount alone matches exactly one outstanding balance

Anything left over is put in front of a person rather than guessed at. A wrong
match is worse than no match: it clears one buyer's debt with another buyer's
money and neither of them finds out until somebody's title does not arrive.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from datetime import date
from enum import Enum

from autozest.errors import ValidationError
from autozest.money import Money
from autozest.settlement.payments import PaymentService
from autozest.settlement.statements import StatementService

REQUIRED_COLUMNS = ("date", "amount", "reference")


class MatchKind(Enum):
    REFERENCE = "reference"
    AMOUNT_AND_DATE = "amount_and_date"
    AMOUNT = "amount"
    NONE = "none"
    AMBIGUOUS = "ambiguous"

    @property
    def is_confident(self) -> bool:
        return self in (MatchKind.REFERENCE, MatchKind.AMOUNT_AND_DATE)


@dataclass(frozen=True)
class BankLine:
    """One line off the bank file."""

    line: int
    on: date
    amount: Money
    reference: str
    detail: str | None = None


@dataclass(frozen=True)
class Match:
    """A bank line and what it looks like."""

    line: BankLine
    kind: MatchKind
    party_id: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def needs_a_person(self) -> bool:
        return self.party_id is None

    def __str__(self) -> str:
        if self.party_id:
            return (
                f"line {self.line.line}  {self.line.amount}  "
                f"{self.party_id} ({self.kind.value})"
            )
        return f"line {self.line.line}  {self.line.amount}  unmatched ({self.kind.value})"


@dataclass(frozen=True)
class Reconciliation:
    """A whole bank file, matched."""

    matches: tuple[Match, ...]

    @property
    def matched(self) -> tuple[Match, ...]:
        return tuple(match for match in self.matches if not match.needs_a_person)

    @property
    def unmatched(self) -> tuple[Match, ...]:
        return tuple(match for match in self.matches if match.needs_a_person)

    @property
    def total(self) -> Money:
        return Money(sum(match.line.amount.cents for match in self.matches))

    def summary(self) -> str:
        return (
            f"{len(self.matches)} lines, {len(self.matched)} matched, "
            f"{len(self.unmatched)} for somebody to look at, {self.total} in total"
        )


def parse(text: str) -> list[BankLine]:
    """Read a bank file. Rejects a file missing a column rather than guessing."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValidationError("the bank file is empty")
    columns = {name.strip().lower() for name in reader.fieldnames}
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise ValidationError(f"the bank file is missing {', '.join(missing)}")
    lines: list[BankLine] = []
    for number, raw in enumerate(reader, start=2):
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        lines.append(
            BankLine(
                line=number,
                on=date.fromisoformat(row["date"]),
                amount=Money.parse(row["amount"]),
                reference=row["reference"],
                detail=row.get("detail") or None,
            )
        )
    return lines


class Reconciler:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.payments = PaymentService(conn)
        self.statements = StatementService(conn)

    def match(self, lines: list[BankLine]) -> Reconciliation:
        """Match every line, hardest evidence first."""
        outstanding = self.statements.outstanding_buyers()
        return Reconciliation(
            matches=tuple(self._match_one(line, outstanding) for line in lines)
        )

    def _match_one(self, line: BankLine, outstanding: dict[str, Money]) -> Match:
        by_reference = self._by_reference(line.reference)
        if by_reference is not None:
            return Match(line=line, kind=MatchKind.REFERENCE, party_id=by_reference)

        exact = [party for party, owed in outstanding.items() if owed == line.amount]
        if len(exact) == 1:
            kind = (
                MatchKind.AMOUNT_AND_DATE
                if self._sold_on(exact[0], line.on)
                else MatchKind.AMOUNT
            )
            return Match(line=line, kind=kind, party_id=exact[0])
        if len(exact) > 1:
            return Match(line=line, kind=MatchKind.AMBIGUOUS, candidates=tuple(sorted(exact)))
        return Match(line=line, kind=MatchKind.NONE)

    def _by_reference(self, reference: str) -> str | None:
        if not reference:
            return None
        row = self.conn.execute(
            "SELECT party_id FROM payment WHERE reference = ? LIMIT 1", (reference,)
        ).fetchone()
        return row["party_id"] if row else None

    def _sold_on(self, party_id: str, on: date) -> bool:
        row = self.conn.execute(
            "SELECT 1 AS found FROM run r JOIN sale s ON s.code = r.sale_code "
            "WHERE r.buyer_id = ? AND s.sale_date <= ? LIMIT 1",
            (party_id, on.isoformat()),
        ).fetchone()
        return row is not None

    def apply(self, reconciliation: Reconciliation) -> int:
        """Take in the money for every confident match. Nothing else."""
        taken = 0
        for match in reconciliation.matches:
            if match.party_id is None or not match.kind.is_confident:
                continue
            self.payments.receive(
                match.party_id, match.line.amount, reference=match.line.reference
            )
            taken += 1
        return taken
