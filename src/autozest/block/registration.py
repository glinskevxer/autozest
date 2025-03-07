"""Bidder registration.

A buyer registers for a sale and gets a badge. The number on it is what the
auctioneer calls out, so it is short and unique within the sale, and it is the
same number week after week because a dealer who has had badge forty for six
years will not answer to anything else.

Registration is where a buyer's problems get caught, and it is the right place
for it. Catching an unpaid balance at the bid means an argument in front of a
room; catching it at the desk on the way in means a quiet conversation. So the
checks that live here are deliberately the ones that would otherwise surface at
the worst possible moment.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.catalog.accounts import AccountRepository
from autozest.errors import Conflict, NotFound, PolicyViolation
from autozest.money import Money
from autozest.settlement.floorplan import FloorplanService
from autozest.settlement.statements import StatementService
from autozest.storage.records import all_rows, insert, now_text, one, update
from autozest.storage.sequences import next_reference

REGISTRATION_PREFIX = "B"

#: A balance older than this stops a buyer registering until it is settled.
BLOCKING_BALANCE = Money.parse("500.00")

FIRST_BADGE = 1
LAST_BADGE = 9999


class RegistrationStatus(Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    WITHDRAWN = "withdrawn"

    @property
    def may_bid(self) -> bool:
        return self is RegistrationStatus.ACTIVE


@dataclass(frozen=True)
class Registration:
    """One buyer, one sale, one badge."""

    id: str
    sale_code: str
    buyer_id: str
    badge: int
    status: RegistrationStatus = RegistrationStatus.ACTIVE
    registered_at: datetime | None = None
    blocked_reason: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Registration:
        return cls(
            id=row["id"],
            sale_code=row["sale_code"],
            buyer_id=row["buyer_id"],
            badge=row["badge"],
            status=RegistrationStatus(row["status"]),
            registered_at=datetime.fromisoformat(row["registered_at"]),
            blocked_reason=row["blocked_reason"],
        )


class RegistrationService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.accounts = AccountRepository(conn)
        self.statements = StatementService(conn)
        self.credit = FloorplanService(conn)

    def blockers(self, buyer_id: str) -> list[str]:
        """Everything that would stop this buyer registering."""
        reasons: list[str] = []
        buyer = self.accounts.get(buyer_id)
        if not buyer.status.may_trade:
            reasons.append(f"the account is {buyer.status.value}")
        if not buyer.account_class.may_bid:
            reasons.append(f"a {buyer.account_class.value} account does not bid")
        owed = self.statements.invoice(buyer_id).balance
        if owed > BLOCKING_BALANCE:
            reasons.append(f"{owed} is outstanding on the account")
        capacity = self.credit.capacity(buyer_id)
        if capacity.limit.cents > 0 and capacity.is_exhausted:
            reasons.append("the floor plan line is used up")
        return reasons

    def register(
        self,
        sale_code: str,
        buyer_id: str,
        *,
        badge: int | None = None,
        force: bool = False,
    ) -> Registration:
        """Put a buyer on the door list and hand them a badge."""
        reasons = self.blockers(buyer_id)
        if reasons and not force:
            raise PolicyViolation(
                "registration.blocked",
                "; ".join(reasons),
                buyer=buyer_id,
                sale=sale_code,
            )
        if self.find(sale_code, buyer_id) is not None:
            raise Conflict(
                "that buyer is already registered for this sale",
                buyer=buyer_id,
                sale=sale_code,
            )
        number = badge or self.badge_for(buyer_id, sale_code)
        reference = next_reference(self.conn, "registration", REGISTRATION_PREFIX)
        insert(
            self.conn,
            "registration",
            {
                "id": reference,
                "sale_code": sale_code,
                "buyer_id": buyer_id,
                "badge": number,
                "status": (
                    RegistrationStatus.BLOCKED.value
                    if reasons
                    else RegistrationStatus.ACTIVE.value
                ),
                "registered_at": now_text(),
                "blocked_reason": "; ".join(reasons) if reasons else None,
            },
        )
        self._remember_badge(buyer_id, number)
        return self.get(reference)

    def badge_for(self, buyer_id: str, sale_code: str) -> int:
        """The buyer's usual badge, or the lowest one free in this sale."""
        row = one(
            self.conn, "SELECT badge FROM preferred_badge WHERE buyer_id = ?", (buyer_id,)
        )
        taken = {registration.badge for registration in self.for_sale(sale_code)}
        if row is not None and row["badge"] not in taken:
            return int(row["badge"])
        for number in range(FIRST_BADGE, LAST_BADGE + 1):
            if number not in taken:
                return number
        raise PolicyViolation(
            "registration.no_badges", "every badge in this sale is taken", sale=sale_code
        )

    def _remember_badge(self, buyer_id: str, badge: int) -> None:
        existing = one(
            self.conn, "SELECT buyer_id FROM preferred_badge WHERE badge = ?", (badge,)
        )
        if existing is not None and existing["buyer_id"] != buyer_id:
            return
        if one(
            self.conn, "SELECT buyer_id FROM preferred_badge WHERE buyer_id = ?", (buyer_id,)
        ):
            update(self.conn, "preferred_badge", {"buyer_id": buyer_id}, {"badge": badge})
            return
        insert(
            self.conn,
            "preferred_badge",
            {"buyer_id": buyer_id, "badge": badge, "since": date.today().isoformat()},
        )

    def get(self, reference: str) -> Registration:
        row = one(self.conn, "SELECT * FROM registration WHERE id = ?", (reference,))
        if row is None:
            raise NotFound("registration", reference)
        return Registration.from_row(row)

    def find(self, sale_code: str, buyer_id: str) -> Registration | None:
        row = one(
            self.conn,
            "SELECT * FROM registration WHERE sale_code = ? AND buyer_id = ?",
            (sale_code, buyer_id),
        )
        return Registration.from_row(row) if row else None

    def by_badge(self, sale_code: str, badge: int) -> Registration:
        row = one(
            self.conn,
            "SELECT * FROM registration WHERE sale_code = ? AND badge = ?",
            (sale_code, badge),
        )
        if row is None:
            raise NotFound("badge", f"{sale_code}/{badge}")
        return Registration.from_row(row)

    def for_sale(self, sale_code: str) -> list[Registration]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM registration WHERE sale_code = ? ORDER BY badge",
            (sale_code,),
        )
        return [Registration.from_row(row) for row in rows]

    def set_status(
        self, reference: str, status: RegistrationStatus, reason: str | None = None
    ) -> Registration:
        self.get(reference)
        update(
            self.conn,
            "registration",
            {"id": reference},
            {"status": status.value, "blocked_reason": reason},
        )
        return self.get(reference)

    def require_registered(self, sale_code: str, buyer_id: str) -> Registration:
        """Raise unless this buyer may bid in this sale."""
        registration = self.find(sale_code, buyer_id)
        if registration is None:
            raise PolicyViolation(
                "registration.missing",
                "that buyer is not registered for this sale",
                buyer=buyer_id,
                sale=sale_code,
            )
        if not registration.status.may_bid:
            raise PolicyViolation(
                "registration.not_active",
                registration.blocked_reason or f"the badge is {registration.status.value}",
                buyer=buyer_id,
                badge=registration.badge,
            )
        return registration
