"""Where a unit physically is.

Six hundred spaces across nine rows and three buildings. A unit nobody can find
is a unit that does not run, and a lane stopped for four minutes while somebody
walks the yard costs more than most of what is in it.

The record is a movement log rather than a column on the consignment. That is
not tidiness: a unit gets moved three or four times between the gate and the
block, and when one goes missing the question is always where it was last seen
and who moved it, which a single current location cannot answer.

A space holds one unit. That is enforced here rather than trusted, because the
one time it was trusted two units were booked into C-14 and the second one was
found a week later.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import Conflict, NotFound, PolicyViolation
from autozest.storage.records import all_rows, insert, now_text, one, scalar, update


class SpaceStatus(Enum):
    FREE = "free"
    OCCUPIED = "occupied"
    BLOCKED = "blocked"

    @property
    def is_usable(self) -> bool:
        return self is SpaceStatus.FREE


@dataclass(frozen=True)
class Space:
    """One parking space."""

    id: str
    zone: str
    row_label: str
    number: int
    covered: bool = False
    status: SpaceStatus = SpaceStatus.FREE

    @property
    def label(self) -> str:
        return f"{self.zone}-{self.row_label}{self.number:03d}"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Space:
        return cls(
            id=row["id"],
            zone=row["zone"],
            row_label=row["row_label"],
            number=row["number"],
            covered=bool(row["covered"]),
            status=SpaceStatus(row["status"]),
        )


@dataclass(frozen=True)
class Movement:
    """One unit going somewhere."""

    id: int
    consignment_id: str
    space_id: str | None
    moved_by: str
    moved_at: datetime
    reason: str | None = None

    @property
    def is_departure(self) -> bool:
        return self.space_id is None


class YardService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.consignments = ConsignmentRepository(conn)

    def add_space(self, space: Space) -> Space:
        insert(
            self.conn,
            "yard_space",
            {
                "id": space.id,
                "zone": space.zone,
                "row_label": space.row_label,
                "number": space.number,
                "covered": int(space.covered),
                "status": space.status.value,
            },
        )
        return self.space(space.id)

    def space(self, space_id: str) -> Space:
        row = one(self.conn, "SELECT * FROM yard_space WHERE id = ?", (space_id,))
        if row is None:
            raise NotFound("yard space", space_id)
        return Space.from_row(row)

    def free_spaces(
        self, zone: str | None = None, *, covered: bool | None = None
    ) -> list[Space]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM yard_space WHERE status = 'free' "
            "AND (? IS NULL OR zone = ?) AND (? IS NULL OR covered = ?) "
            "ORDER BY zone, row_label, number",
            (
                zone,
                zone,
                None if covered is None else int(covered),
                None if covered is None else int(covered),
            ),
        )
        return [Space.from_row(row) for row in rows]

    def block(self, space_id: str, reason: str) -> Space:
        """Take a space out of use, for a skip or a broken surface."""
        space = self.space(space_id)
        if space.status is SpaceStatus.OCCUPIED:
            raise PolicyViolation(
                "yard.occupied",
                "move the unit out before blocking the space",
                space=space.label,
            )
        update(self.conn, "yard_space", {"id": space_id}, {"status": SpaceStatus.BLOCKED.value})
        return self.space(space_id)

    def unblock(self, space_id: str) -> Space:
        update(self.conn, "yard_space", {"id": space_id}, {"status": SpaceStatus.FREE.value})
        return self.space(space_id)

    def put(
        self, consignment_id: str, space_id: str, moved_by: str, reason: str | None = None
    ) -> Movement:
        """Park a unit. One unit to a space, and it is checked."""
        self.consignments.get(consignment_id)
        space = self.space(space_id)
        if not space.status.is_usable:
            raise Conflict(
                f"{space.label} is {space.status.value}",
                space=space.label,
                occupant=self.occupant(space_id),
            )
        previous = self.where_is(consignment_id)
        if previous is not None:
            self._free(previous)
        insert(
            self.conn,
            "yard_movement",
            {
                "consignment_id": consignment_id,
                "space_id": space_id,
                "moved_by": moved_by,
                "reason": reason,
                "moved_at": now_text(),
            },
        )
        update(
            self.conn, "yard_space", {"id": space_id}, {"status": SpaceStatus.OCCUPIED.value}
        )
        return self.history(consignment_id)[-1]

    def take_out(self, consignment_id: str, moved_by: str, reason: str) -> Movement:
        """Record a unit leaving the yard entirely."""
        space_id = self.where_is(consignment_id)
        if space_id is None:
            raise PolicyViolation(
                "yard.not_here", "that unit is not in the yard", consignment=consignment_id
            )
        self._free(space_id)
        insert(
            self.conn,
            "yard_movement",
            {
                "consignment_id": consignment_id,
                "space_id": None,
                "moved_by": moved_by,
                "reason": reason,
                "moved_at": now_text(),
            },
        )
        return self.history(consignment_id)[-1]

    def _free(self, space_id: str) -> None:
        update(self.conn, "yard_space", {"id": space_id}, {"status": SpaceStatus.FREE.value})

    def where_is(self, consignment_id: str) -> str | None:
        """The space a unit is in now, or None if it has left."""
        row = one(
            self.conn,
            "SELECT space_id FROM yard_movement WHERE consignment_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (consignment_id,),
        )
        return row["space_id"] if row else None

    def occupant(self, space_id: str) -> str | None:
        row = one(
            self.conn,
            "SELECT consignment_id FROM yard_movement WHERE space_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (space_id,),
        )
        if row is None:
            return None
        occupant = row["consignment_id"]
        return occupant if self.where_is(occupant) == space_id else None

    def history(self, consignment_id: str) -> list[Movement]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM yard_movement WHERE consignment_id = ? ORDER BY id",
            (consignment_id,),
        )
        return [
            Movement(
                id=row["id"],
                consignment_id=row["consignment_id"],
                space_id=row["space_id"],
                moved_by=row["moved_by"],
                reason=row["reason"],
                moved_at=datetime.fromisoformat(row["moved_at"]),
            )
            for row in rows
        ]

    def occupancy(self) -> dict[SpaceStatus, int]:
        rows = all_rows(
            self.conn, "SELECT status, COUNT(*) AS n FROM yard_space GROUP BY status"
        )
        return {SpaceStatus(row["status"]): row["n"] for row in rows}

    def capacity(self) -> int:
        return int(scalar(self.conn, "SELECT COUNT(*) FROM yard_space") or 0)
