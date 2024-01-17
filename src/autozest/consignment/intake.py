"""Intake: registering a unit, and receiving it when it turns up.

Registration and arrival are two different events and there is often a fortnight
between them. A fleet books forty units into next month's sale on a spreadsheet
and thirty seven of them arrive, so the register step has to work with nothing
but a vin and a seller, and the gate step has to work with a truck driver
holding a set of keys and no reference number.

That is why the gate pass exists and why it is looked up by pass, by vin and by
reference. The gate has whichever one of those the driver brought.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from autozest.catalog.accounts import AccountRepository
from autozest.catalog.odometer import Reading
from autozest.catalog.vehicles import VehicleRepository
from autozest.consignment.models import Consignment, ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import NotFound, PolicyViolation, ValidationError
from autozest.identifiers import gate_pass
from autozest.money import Money
from autozest.storage.records import all_rows, scalar, update


@dataclass(frozen=True)
class Arrival:
    """What the gate records when a unit comes off the truck."""

    keys_count: int
    miles: int
    source: str = "gate"
    digits: int = 6
    note: str | None = None

    def __post_init__(self) -> None:
        if self.keys_count < 0:
            raise ValidationError("a negative key count is not a thing")


class IntakeService:
    """Everything that happens before the unit is inspected."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.accounts = AccountRepository(conn)
        self.vehicles = VehicleRepository(conn)
        self.consignments = ConsignmentRepository(conn)

    def register(
        self,
        vin: str,
        seller_id: str,
        *,
        floor: Money | None = None,
        transport_ref: str | None = None,
        title_expected: bool = True,
        note: str | None = None,
        when: date | None = None,
    ) -> Consignment:
        """Book a unit into the sale.

        The seller has to be allowed to consign, the unit has to be on file,
        and it must not already be here under someone else's name.
        """
        seller = self.accounts.get(seller_id)
        seller.require_can_consign()
        if self.vehicles.find(vin) is None:
            raise NotFound("vehicle", vin)
        self.consignments.require_no_open_consignment(vin)
        reference = self.consignments.next_id(when)
        return self.consignments.add(
            Consignment(
                id=reference,
                vin=vin,
                seller_id=seller_id,
                floor=floor,
                floor_set_at=datetime.now().astimezone() if floor else None,
                transport_ref=transport_ref,
                title_expected=title_expected,
                note=note,
            )
        )

    def check_in(self, reference: str, arrival: Arrival) -> Consignment:
        """Receive the unit, record what the gate read, and issue a pass.

        The odometer reading taken here is the one an arbitration argues about
        months later, so it is written to the vehicle history rather than being
        kept on the consignment.
        """
        consignment = self.consignments.get(reference)
        if consignment.status is not ConsignmentStatus.REGISTERED:
            raise PolicyViolation(
                "intake.already_arrived",
                "that unit is past the gate",
                consignment=reference,
                status=consignment.status.value,
            )
        self.vehicles.record_reading(
            consignment.vin,
            Reading(
                miles=arrival.miles,
                read_on=date.today(),
                source=arrival.source,
                digits=arrival.digits,
            ),
        )
        arrived = self.consignments.save_status(consignment, ConsignmentStatus.ARRIVED)
        pass_text = gate_pass(self._next_gate_sequence())
        update(
            self.conn,
            "consignment",
            {"id": reference},
            {
                "keys_count": arrival.keys_count,
                "gate_pass": pass_text,
                "arrived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "note": arrival.note or arrived.note,
            },
        )
        return self.consignments.get(reference)

    def _next_gate_sequence(self) -> int:
        used = int(
            scalar(self.conn, "SELECT COUNT(*) FROM consignment WHERE gate_pass IS NOT NULL")
            or 0
        )
        return used + 1

    def find(self, token: str) -> Consignment:
        """Look a unit up by reference, by vin or by gate pass.

        The driver at the gate has exactly one of these and it is never the
        same one twice.
        """
        cleaned = token.strip().upper()
        rows = all_rows(
            self.conn,
            "SELECT * FROM consignment WHERE id = ? OR vin = ? OR gate_pass = ? "
            "ORDER BY CASE WHEN status IN ('sold', 'withdrawn', 'released') "
            "THEN 1 ELSE 0 END, id",
            (cleaned, cleaned, cleaned),
        )
        if not rows:
            raise NotFound("consignment", token)
        return Consignment.from_row(rows[0])

    def withdraw(self, reference: str, reason: str) -> Consignment:
        """Pull a unit out of the sale. Allowed until it is sold."""
        if not reason.strip():
            raise ValidationError("a withdrawal needs a reason")
        consignment = self.consignments.get(reference)
        withdrawn = self.consignments.save_status(consignment, ConsignmentStatus.WITHDRAWN)
        update(self.conn, "consignment", {"id": reference}, {"note": reason})
        return self.consignments.get(withdrawn.id)

    def release(self, reference: str) -> Consignment:
        """Hand a unit that did not sell back to its seller."""
        consignment = self.consignments.get(reference)
        if consignment.status is not ConsignmentStatus.NO_SALE:
            raise PolicyViolation(
                "intake.release",
                "only a unit that did not sell can be released back",
                consignment=reference,
                status=consignment.status.value,
            )
        return self.consignments.save_status(consignment, ConsignmentStatus.RELEASED)

    def expected_arrivals(self) -> list[Consignment]:
        """Everything registered that has not come through the gate."""
        return self.consignments.with_status(ConsignmentStatus.REGISTERED)

    def on_the_ground(self) -> list[Consignment]:
        """Everything physically here, whatever stage it is at."""
        rows = all_rows(
            self.conn,
            "SELECT * FROM consignment WHERE status IN "
            "('arrived', 'inspected', 'catalogued', 'no_sale') ORDER BY id",
        )
        return [Consignment.from_row(row) for row in rows]
