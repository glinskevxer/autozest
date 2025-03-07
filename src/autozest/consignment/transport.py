"""Transport orders.

Units arrive on a truck and about a third of them leave on one. The order hangs
off the consignment rather than the run, because it is booked before anybody
knows whether the unit will sell and a unit that does not sell still has to get
home.

Who pays is on the order and is not derived. It looks like it should be: the
buyer pays outbound, the seller pays inbound. In practice a seller often eats
the delivery to close a deal, and a fleet's contract has the auction covering
inbound on anything over a hundred units a year. Deriving it produced an
invoice that was wrong about a quarter of the time, so it is recorded.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import NotFound, PolicyViolation, StateError, ValidationError
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, one, update
from autozest.storage.sequences import next_reference

TRANSPORT_PREFIX = "T"


class Direction(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class TransportStatus(Enum):
    BOOKED = "booked"
    COLLECTED = "collected"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @property
    def is_finished(self) -> bool:
        return self in (TransportStatus.DELIVERED, TransportStatus.CANCELLED)


TRANSITIONS: dict[TransportStatus, frozenset[TransportStatus]] = {
    TransportStatus.BOOKED: frozenset({TransportStatus.COLLECTED, TransportStatus.CANCELLED}),
    TransportStatus.COLLECTED: frozenset({TransportStatus.DELIVERED}),
    TransportStatus.DELIVERED: frozenset(),
    TransportStatus.CANCELLED: frozenset(),
}


class Payer(Enum):
    BUYER = "buyer"
    SELLER = "seller"
    AUCTION = "auction"


@dataclass(frozen=True)
class Carrier:
    id: str
    name: str
    contact: str | None = None
    active: bool = True


@dataclass(frozen=True)
class TransportOrder:
    """One movement of one unit."""

    id: str
    consignment_id: str
    direction: Direction
    origin: str
    destination: str
    payer: Payer
    status: TransportStatus = TransportStatus.BOOKED
    carrier_id: str | None = None
    quoted: Money | None = None
    booked_on: date | None = None
    collected_on: date | None = None
    delivered_on: date | None = None
    note: str | None = None

    @property
    def is_open(self) -> bool:
        return not self.status.is_finished

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TransportOrder:
        return cls(
            id=row["id"],
            consignment_id=row["consignment_id"],
            direction=Direction(row["direction"]),
            origin=row["origin"],
            destination=row["destination"],
            payer=Payer(row["payer"]),
            status=TransportStatus(row["status"]),
            carrier_id=row["carrier_id"],
            quoted=Money(row["quoted_cents"]) if row["quoted_cents"] is not None else None,
            booked_on=date.fromisoformat(row["booked_on"]),
            collected_on=(
                date.fromisoformat(row["collected_on"]) if row["collected_on"] else None
            ),
            delivered_on=(
                date.fromisoformat(row["delivered_on"]) if row["delivered_on"] else None
            ),
            note=row["note"],
        )


class TransportService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.consignments = ConsignmentRepository(conn)

    def add_carrier(self, carrier: Carrier) -> Carrier:
        insert(
            self.conn,
            "carrier",
            {
                "id": carrier.id,
                "name": carrier.name,
                "contact": carrier.contact,
                "status": "active" if carrier.active else "suspended",
                "created_at": now_text(),
            },
        )
        return carrier

    def carriers(self) -> list[Carrier]:
        rows = all_rows(self.conn, "SELECT * FROM carrier ORDER BY name")
        return [
            Carrier(
                id=row["id"],
                name=row["name"],
                contact=row["contact"],
                active=row["status"] == "active",
            )
            for row in rows
        ]

    def book(
        self,
        consignment_id: str,
        direction: Direction,
        origin: str,
        destination: str,
        payer: Payer,
        *,
        carrier_id: str | None = None,
        quoted: Money | None = None,
        on: date | None = None,
        note: str | None = None,
    ) -> TransportOrder:
        """Book a movement. One open order per direction per unit."""
        self.consignments.get(consignment_id)
        if not origin.strip() or not destination.strip():
            raise ValidationError("a transport order needs both ends")
        for existing in self.for_consignment(consignment_id):
            if existing.direction is direction and existing.is_open:
                raise PolicyViolation(
                    "transport.already_booked",
                    f"there is already an open {direction.value} order on this unit",
                    consignment=consignment_id,
                    order=existing.id,
                )
        day = on or date.today()
        reference = next_reference(self.conn, "transport_order", TRANSPORT_PREFIX, on=day)
        insert(
            self.conn,
            "transport_order",
            {
                "id": reference,
                "consignment_id": consignment_id,
                "carrier_id": carrier_id,
                "direction": direction.value,
                "status": TransportStatus.BOOKED.value,
                "origin": origin,
                "destination": destination,
                "quoted_cents": quoted.cents if quoted else None,
                "payer": payer.value,
                "booked_on": day.isoformat(),
                "note": note,
            },
        )
        return self.get(reference)

    def get(self, reference: str) -> TransportOrder:
        row = one(self.conn, "SELECT * FROM transport_order WHERE id = ?", (reference,))
        if row is None:
            raise NotFound("transport order", reference)
        return TransportOrder.from_row(row)

    def for_consignment(self, consignment_id: str) -> list[TransportOrder]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM transport_order WHERE consignment_id = ? ORDER BY id",
            (consignment_id,),
        )
        return [TransportOrder.from_row(row) for row in rows]

    def move(
        self, reference: str, status: TransportStatus, *, on: date | None = None
    ) -> TransportOrder:
        order = self.get(reference)
        if status not in TRANSITIONS[order.status]:
            allowed = ", ".join(sorted(s.value for s in TRANSITIONS[order.status])) or "nothing"
            raise StateError(f"transport order {reference}", order.status.value, allowed)
        column = {
            TransportStatus.COLLECTED: "collected_on",
            TransportStatus.DELIVERED: "delivered_on",
        }.get(status)
        values: dict[str, object] = {"status": status.value}
        if column:
            values[column] = (on or date.today()).isoformat()
        update(self.conn, "transport_order", {"id": reference}, values)
        return self.get(reference)

    def outstanding(self, direction: Direction | None = None) -> list[TransportOrder]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM transport_order WHERE status IN ('booked', 'collected') "
            "AND (? IS NULL OR direction = ?) ORDER BY booked_on, id",
            (direction.value if direction else None, direction.value if direction else None),
        )
        return [TransportOrder.from_row(row) for row in rows]
