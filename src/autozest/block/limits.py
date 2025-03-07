"""Caps a buyer asked for, or had imposed.

Two different things behave identically at the block, so they share a table. A
limit a dealer asked us to hold them to and a limit the credit desk set both
stop a bid; only the reason differs. Keeping the reason is what makes the
conversation afterwards possible, and it is a completely different conversation.

Three scopes, and they are checked together. A buyer with a four thousand per
unit cap and a twenty thousand per sale cap can buy five units at four thousand
and no more, which is the arrangement most of the smaller dealers actually
want.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.errors import NotFound, PolicyViolation, ValidationError
from autozest.money import Money, total
from autozest.storage.records import all_rows, insert, one, update
from autozest.storage.sequences import next_reference

LIMIT_PREFIX = "L"


class Scope(Enum):
    PER_UNIT = "per_unit"
    PER_SALE = "per_sale"
    PER_DAY = "per_day"

    @property
    def counts_spending(self) -> bool:
        """Whether the check adds up what has already been bought."""
        return self is not Scope.PER_UNIT


@dataclass(frozen=True)
class PurchaseLimit:
    """One cap on one buyer."""

    id: str
    buyer_id: str
    scope: Scope
    amount: Money
    reason: str
    set_by: str
    imposed: bool = False
    set_on: date | None = None
    lifted_on: date | None = None

    @property
    def is_live(self) -> bool:
        return self.lifted_on is None

    def describe(self) -> str:
        kind = "imposed" if self.imposed else "asked for"
        return f"{self.amount} {self.scope.value}, {kind}: {self.reason}"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PurchaseLimit:
        return cls(
            id=row["id"],
            buyer_id=row["buyer_id"],
            scope=Scope(row["scope"]),
            amount=Money(row["amount_cents"]),
            reason=row["reason"],
            set_by=row["set_by"],
            imposed=bool(row["imposed"]),
            set_on=date.fromisoformat(row["set_on"]),
            lifted_on=date.fromisoformat(row["lifted_on"]) if row["lifted_on"] else None,
        )


class LimitService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sales = SaleRepository(conn)

    def set_limit(
        self,
        buyer_id: str,
        scope: Scope,
        amount: Money,
        *,
        reason: str,
        set_by: str,
        imposed: bool = False,
        on: date | None = None,
    ) -> PurchaseLimit:
        if amount.is_negative:
            raise ValidationError("a limit cannot be negative")
        if not reason.strip():
            raise ValidationError("a limit needs a reason")
        existing = self.live(buyer_id, scope)
        if existing is not None:
            self.lift(existing.id, on=on)
        reference = next_reference(self.conn, "purchase_limit", LIMIT_PREFIX, on=on)
        insert(
            self.conn,
            "purchase_limit",
            {
                "id": reference,
                "buyer_id": buyer_id,
                "scope": scope.value,
                "amount_cents": amount.cents,
                "imposed": int(imposed),
                "reason": reason,
                "set_by": set_by,
                "set_on": (on or date.today()).isoformat(),
            },
        )
        return self.get(reference)

    def get(self, reference: str) -> PurchaseLimit:
        row = one(self.conn, "SELECT * FROM purchase_limit WHERE id = ?", (reference,))
        if row is None:
            raise NotFound("purchase limit", reference)
        return PurchaseLimit.from_row(row)

    def live(self, buyer_id: str, scope: Scope | None = None) -> PurchaseLimit | None:
        row = one(
            self.conn,
            "SELECT * FROM purchase_limit WHERE buyer_id = ? AND lifted_on IS NULL "
            "AND (? IS NULL OR scope = ?) ORDER BY id DESC LIMIT 1",
            (buyer_id, scope.value if scope else None, scope.value if scope else None),
        )
        return PurchaseLimit.from_row(row) if row else None

    def all_live(self, buyer_id: str) -> list[PurchaseLimit]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM purchase_limit WHERE buyer_id = ? AND lifted_on IS NULL "
            "ORDER BY scope",
            (buyer_id,),
        )
        return [PurchaseLimit.from_row(row) for row in rows]

    def lift(self, reference: str, *, on: date | None = None) -> PurchaseLimit:
        self.get(reference)
        update(
            self.conn,
            "purchase_limit",
            {"id": reference},
            {"lifted_on": (on or date.today()).isoformat()},
        )
        return self.get(reference)

    def spent_in_sale(self, buyer_id: str, sale_code: str) -> Money:
        return total(
            [
                run.hammer
                for run in self.sales.runs(sale_code)
                if run.status is RunStatus.SOLD
                and run.buyer_id == buyer_id
                and run.hammer is not None
            ]
        )

    def spent_on(self, buyer_id: str, day: date) -> Money:
        rows = all_rows(
            self.conn,
            "SELECT r.hammer_cents FROM run r JOIN sale s ON s.code = r.sale_code "
            "WHERE r.buyer_id = ? AND r.status = 'sold' AND s.sale_date = ?",
            (buyer_id, day.isoformat()),
        )
        return total([Money(row["hammer_cents"]) for row in rows])

    def check(self, buyer_id: str, sale_code: str, amount: Money) -> None:
        """Raise if this bid would take the buyer past any live cap."""
        limits = self.all_live(buyer_id)
        if not limits:
            return
        sale = self.sales.get(sale_code)
        for limit in limits:
            already = {
                Scope.PER_UNIT: Money.zero(),
                Scope.PER_SALE: self.spent_in_sale(buyer_id, sale_code),
                Scope.PER_DAY: self.spent_on(buyer_id, sale.sale_date),
            }[limit.scope]
            if already + amount > limit.amount:
                raise PolicyViolation(
                    "limit.exceeded",
                    f"that goes past the {limit.describe()}",
                    buyer=buyer_id,
                    scope=limit.scope.value,
                    limit=str(limit.amount),
                    already=str(already),
                )
