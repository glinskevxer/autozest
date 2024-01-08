"""Reads and writes for consignments.

The one thing worth knowing about this module is that it never invents a
status. Every status change goes through the state machine in
:mod:`autozest.consignment.models` and the repository only persists the answer.
Keeping the rule in one place is what stops a unit being catalogued twice from
two different code paths.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from autozest.consignment.models import Consignment, ConsignmentStatus
from autozest.errors import Conflict, PolicyViolation
from autozest.identifiers import consignment_id, parse_sequenced
from autozest.money import Money
from autozest.storage.records import (
    all_rows,
    insert,
    now_text,
    one,
    one_or_raise,
    scalar,
    update,
)


class ConsignmentRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def next_id(self, when: date | None = None) -> str:
        """The next reference for the year, taking the highest already used.

        Reading the maximum rather than keeping a counter means an import that
        inserts references directly cannot collide with the next one issued.
        """
        day = when or date.today()
        rows = all_rows(
            self.conn,
            "SELECT id FROM consignment WHERE id LIKE ?",
            (f"C-{day.year:04d}-%",),
        )
        highest = 0
        for row in rows:
            _, _, sequence = parse_sequenced(row["id"])
            highest = max(highest, sequence)
        return consignment_id(day, highest + 1)

    def add(self, consignment: Consignment) -> Consignment:
        self.require_no_open_consignment(consignment.vin)
        insert(
            self.conn,
            "consignment",
            {
                "id": consignment.id,
                "vin": consignment.vin,
                "seller_id": consignment.seller_id,
                "status": consignment.status.value,
                "floor_cents": consignment.floor.cents if consignment.floor else None,
                "floor_set_at": _text(consignment.floor_set_at),
                "keys_count": consignment.keys_count,
                "gate_pass": consignment.gate_pass,
                "transport_ref": consignment.transport_ref,
                "title_expected": int(consignment.title_expected),
                "arrived_at": _text(consignment.arrived_at),
                "closed_at": _text(consignment.closed_at),
                "note": consignment.note,
                "created_at": now_text(),
            },
        )
        return consignment

    def get(self, reference: str) -> Consignment:
        row = one_or_raise(
            self.conn,
            "SELECT * FROM consignment WHERE id = ?",
            (reference,),
            "consignment",
            reference,
        )
        return Consignment.from_row(row)

    def find_open_for_vin(self, vin: str) -> Consignment | None:
        """The live consignment for a vin, if there is one."""
        row = one(
            self.conn,
            "SELECT * FROM consignment WHERE vin = ? "
            "AND status NOT IN ('sold', 'withdrawn', 'released')",
            (vin,),
        )
        return Consignment.from_row(row) if row else None

    def require_no_open_consignment(self, vin: str) -> None:
        existing = self.find_open_for_vin(vin)
        if existing is not None:
            raise Conflict(
                "that vin is already consigned and has not left",
                vin=vin,
                consignment=existing.id,
                status=existing.status.value,
            )

    def history_for_vin(self, vin: str) -> list[Consignment]:
        """Every consignment the vin has ever had, oldest first."""
        rows = all_rows(
            self.conn, "SELECT * FROM consignment WHERE vin = ? ORDER BY created_at, id", (vin,)
        )
        return [Consignment.from_row(row) for row in rows]

    def for_seller(
        self, seller_id: str, status: ConsignmentStatus | None = None
    ) -> list[Consignment]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM consignment WHERE seller_id = ? AND (? IS NULL OR status = ?) "
            "ORDER BY id",
            (seller_id, status.value if status else None, status.value if status else None),
        )
        return [Consignment.from_row(row) for row in rows]

    def with_status(self, status: ConsignmentStatus) -> list[Consignment]:
        rows = all_rows(
            self.conn, "SELECT * FROM consignment WHERE status = ? ORDER BY id", (status.value,)
        )
        return [Consignment.from_row(row) for row in rows]

    def save_status(self, consignment: Consignment, status: ConsignmentStatus) -> Consignment:
        """Move a consignment, letting the model decide whether it may move."""
        moved = consignment.with_status(status)
        closed = None if moved.status.is_open else now_text()
        update(
            self.conn,
            "consignment",
            {"id": moved.id},
            {"status": moved.status.value, "closed_at": closed},
        )
        return self.get(moved.id)

    def set_floor(
        self, reference: str, floor: Money, *, at: datetime | None = None
    ) -> Consignment:
        """Set or move the reserve.

        A floor can come down at any point before the unit runs. It can only go
        up while the unit is still being worked, because once it is catalogued
        the number is in the buyer's hands and moving it upward changes the
        deal after the fact.
        """
        current = self.get(reference)
        if floor.is_negative:
            raise PolicyViolation(
                "floor.negative", "a floor cannot be negative", floor=str(floor)
            )
        if not current.status.is_open:
            raise PolicyViolation(
                "floor.closed",
                "that consignment is closed",
                consignment=reference,
                status=current.status.value,
            )
        raising = current.floor is not None and floor > current.floor
        if raising and current.status is ConsignmentStatus.CATALOGUED:
            raise PolicyViolation(
                "floor.raise_after_catalogue",
                "the floor cannot be raised once the unit is catalogued",
                consignment=reference,
                was=str(current.floor),
                wanted=str(floor),
            )
        update(
            self.conn,
            "consignment",
            {"id": reference},
            {
                "floor_cents": floor.cents,
                "floor_set_at": _text(at or datetime.now().astimezone()),
            },
        )
        return self.get(reference)

    def count(self) -> int:
        return int(scalar(self.conn, "SELECT COUNT(*) FROM consignment") or 0)


def _text(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None
