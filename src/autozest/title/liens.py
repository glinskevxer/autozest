"""Lienholders and payoff quotes.

A lien is somebody else's claim on the title. It comes off before the title
goes anywhere, and the amount is only good for a few days because interest is
still running.

That last part is the whole reason this is a record with a date on it. A stale
quote paid in full still leaves a lien on the title: the lender takes the money,
applies it, and the balance is eleven dollars of interest that nobody sent. The
title stays where it is and everybody finds out three weeks later.

So a quote knows its per diem and can be brought forward to today, and paying
one is checked against what it is worth on the day the cheque goes out rather
than the day somebody rang up.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import NotFound, PolicyViolation, ValidationError
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, one, update
from autozest.storage.sequences import next_reference

QUOTE_PREFIX = "Q"

#: Money is immutable, so one shared zero is safe as a field default.
ZERO = Money.zero()


@dataclass(frozen=True)
class Lienholder:
    id: str
    name: str
    contact: str | None = None


@dataclass(frozen=True)
class PayoffQuote:
    """What a lender says it takes to clear the lien, and until when."""

    id: str
    consignment_id: str
    lienholder_id: str
    amount: Money
    good_until: date
    quoted_on: date
    per_diem: Money = ZERO
    paid_on: date | None = None
    paid: Money | None = None

    @property
    def is_paid(self) -> bool:
        return self.paid_on is not None

    def is_current(self, on: date) -> bool:
        return on <= self.good_until

    def amount_on(self, day: date) -> Money:
        """What it takes to clear it on a given day.

        Inside the quoted window it is the quoted figure. Past it, the per diem
        has been running and the quoted figure is short.
        """
        if day <= self.good_until:
            return self.amount
        days_over = (day - self.good_until).days
        return self.amount + self.per_diem.times(days_over)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PayoffQuote:
        return cls(
            id=row["id"],
            consignment_id=row["consignment_id"],
            lienholder_id=row["lienholder_id"],
            amount=Money(row["amount_cents"]),
            per_diem=Money(row["per_diem_cents"]),
            good_until=date.fromisoformat(row["good_until"]),
            quoted_on=date.fromisoformat(row["quoted_on"]),
            paid_on=date.fromisoformat(row["paid_on"]) if row["paid_on"] else None,
            paid=Money(row["paid_cents"]) if row["paid_cents"] is not None else None,
        )


class LienService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.consignments = ConsignmentRepository(conn)

    def add_lienholder(self, lienholder: Lienholder) -> Lienholder:
        insert(
            self.conn,
            "lienholder",
            {
                "id": lienholder.id,
                "name": lienholder.name,
                "contact": lienholder.contact,
                "created_at": now_text(),
            },
        )
        return lienholder

    def lienholders(self) -> list[Lienholder]:
        rows = all_rows(self.conn, "SELECT * FROM lienholder ORDER BY name")
        return [
            Lienholder(id=row["id"], name=row["name"], contact=row["contact"]) for row in rows
        ]

    def quote(
        self,
        consignment_id: str,
        lienholder_id: str,
        amount: Money,
        good_until: date,
        *,
        per_diem: Money | None = None,
        on: date | None = None,
    ) -> PayoffQuote:
        """Record what the lender said."""
        self.consignments.get(consignment_id)
        if amount.is_negative:
            raise ValidationError("a payoff cannot be negative")
        quoted_on = on or date.today()
        if good_until < quoted_on:
            raise ValidationError("a quote cannot expire before it was given")
        reference = next_reference(self.conn, "payoff_quote", QUOTE_PREFIX, on=quoted_on)
        insert(
            self.conn,
            "payoff_quote",
            {
                "id": reference,
                "consignment_id": consignment_id,
                "lienholder_id": lienholder_id,
                "amount_cents": amount.cents,
                "per_diem_cents": (per_diem or Money.zero()).cents,
                "good_until": good_until.isoformat(),
                "quoted_on": quoted_on.isoformat(),
            },
        )
        return self.get(reference)

    def get(self, reference: str) -> PayoffQuote:
        row = one(self.conn, "SELECT * FROM payoff_quote WHERE id = ?", (reference,))
        if row is None:
            raise NotFound("payoff quote", reference)
        return PayoffQuote.from_row(row)

    def latest(self, consignment_id: str) -> PayoffQuote | None:
        row = one(
            self.conn,
            "SELECT * FROM payoff_quote WHERE consignment_id = ? "
            "ORDER BY quoted_on DESC, id DESC LIMIT 1",
            (consignment_id,),
        )
        return PayoffQuote.from_row(row) if row else None

    def outstanding(self) -> list[PayoffQuote]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM payoff_quote WHERE paid_on IS NULL ORDER BY good_until",
        )
        return [PayoffQuote.from_row(row) for row in rows]

    def stale(self, on: date | None = None) -> list[PayoffQuote]:
        """Quotes that have run past their date and need re-asking."""
        today = on or date.today()
        return [quote for quote in self.outstanding() if not quote.is_current(today)]

    def pay(self, reference: str, amount: Money, *, on: date | None = None) -> PayoffQuote:
        """Record a payoff, checked against what it is worth today.

        A short payment leaves the lien in place, so it is refused rather than
        recorded and discovered later.
        """
        quote = self.get(reference)
        if quote.is_paid:
            raise PolicyViolation(
                "lien.already_paid", "that payoff has been made", quote=reference
            )
        day = on or date.today()
        owed = quote.amount_on(day)
        if amount < owed:
            raise PolicyViolation(
                "lien.short",
                f"that is {owed - amount} short of what clears the lien on {day}",
                quote=reference,
                owed=str(owed),
                offered=str(amount),
            )
        update(
            self.conn,
            "payoff_quote",
            {"id": reference},
            {"paid_on": day.isoformat(), "paid_cents": amount.cents},
        )
        return self.get(reference)
