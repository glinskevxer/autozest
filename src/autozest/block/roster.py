"""Who is on which lane.

An auctioneer works one lane at a time and a ring man works one auctioneer.
Double booking either is not a scheduling inconvenience, it is a lane that does
not run, so the overlap is checked here rather than trusted to whoever is
holding the clipboard.

A lane needs an auctioneer and at least one ring man before it opens. That is
the only staffing rule the system enforces, because it is the only one where
being wrong stops the sale rather than making it harder.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any

from autozest.block.sales import SaleRepository
from autozest.errors import NotFound, PolicyViolation
from autozest.storage.records import all_rows, insert, now_text, one, update


class Role(Enum):
    AUCTIONEER = "auctioneer"
    RING = "ring"
    CLERK = "clerk"
    INSPECTOR = "inspector"
    TITLE = "title"
    DRIVER = "driver"

    @property
    def works_a_lane(self) -> bool:
        """Roles that can only be in one lane at a time."""
        return self in (Role.AUCTIONEER, Role.RING, Role.CLERK)


#: What a lane needs before it can open.
REQUIRED = {Role.AUCTIONEER: 1, Role.RING: 1}


@dataclass(frozen=True)
class Staff:
    id: str
    name: str
    role: Role
    active: bool = True

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Staff:
        return cls(
            id=row["id"],
            name=row["name"],
            role=Role(row["role"]),
            active=bool(row["active"]),
        )


@dataclass(frozen=True)
class Assignment:
    sale_code: str
    lane: str
    staff_id: str
    role: Role


class RosterService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sales = SaleRepository(conn)

    def add(self, person: Staff) -> Staff:
        insert(
            self.conn,
            "staff",
            {
                "id": person.id,
                "name": person.name,
                "role": person.role.value,
                "active": int(person.active),
                "created_at": now_text(),
            },
        )
        return self.person(person.id)

    def person(self, staff_id: str) -> Staff:
        row = one(self.conn, "SELECT * FROM staff WHERE id = ?", (staff_id,))
        if row is None:
            raise NotFound("staff", staff_id)
        return Staff.from_row(row)

    def people(self, role: Role | None = None) -> list[Staff]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM staff WHERE (? IS NULL OR role = ?) ORDER BY name",
            (role.value if role else None, role.value if role else None),
        )
        return [Staff.from_row(row) for row in rows]

    def retire(self, staff_id: str) -> Staff:
        self.person(staff_id)
        update(self.conn, "staff", {"id": staff_id}, {"active": 0})
        return self.person(staff_id)

    def assign(self, sale_code: str, lane: str, staff_id: str) -> Assignment:
        """Put somebody on a lane, checking they are not already on another."""
        person = self.person(staff_id)
        if not person.active:
            raise PolicyViolation(
                "roster.retired", f"{person.name} is not on the roster", staff=staff_id
            )
        target = self.sales.lane(sale_code, lane)
        if person.role.works_a_lane:
            elsewhere = [
                assignment
                for assignment in self.for_staff(staff_id, sale_code)
                if assignment.lane != target.letter
            ]
            if elsewhere:
                raise PolicyViolation(
                    "roster.double_booked",
                    f"{person.name} is already on lane {elsewhere[0].lane}",
                    staff=staff_id,
                    sale=sale_code,
                )
        insert(
            self.conn,
            "lane_assignment",
            {
                "sale_code": sale_code,
                "lane": target.letter,
                "staff_id": staff_id,
                "role": person.role.value,
            },
        )
        return Assignment(sale_code, target.letter, staff_id, person.role)

    def unassign(self, sale_code: str, lane: str, staff_id: str) -> None:
        self.conn.execute(
            "DELETE FROM lane_assignment WHERE sale_code = ? AND lane = ? AND staff_id = ?",
            (sale_code, lane.upper(), staff_id),
        )

    def on_lane(self, sale_code: str, lane: str) -> list[Assignment]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM lane_assignment WHERE sale_code = ? AND lane = ? ORDER BY role, id",
            (sale_code, lane.upper()),
        )
        return [
            Assignment(row["sale_code"], row["lane"], row["staff_id"], Role(row["role"]))
            for row in rows
        ]

    def for_staff(self, staff_id: str, sale_code: str) -> list[Assignment]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM lane_assignment WHERE staff_id = ? AND sale_code = ?",
            (staff_id, sale_code),
        )
        return [
            Assignment(row["sale_code"], row["lane"], row["staff_id"], Role(row["role"]))
            for row in rows
        ]

    def gaps(self, sale_code: str) -> dict[str, list[str]]:
        """Every lane that cannot open yet, and what it is short of."""
        short: dict[str, list[str]] = {}
        for lane in self.sales.lanes(sale_code):
            crew = self.on_lane(sale_code, lane.letter)
            missing = [
                f"{needed} {role.value}"
                for role, needed in REQUIRED.items()
                if sum(1 for member in crew if member.role is role) < needed
            ]
            if missing:
                short[lane.letter] = missing
        return short

    def require_staffed(self, sale_code: str) -> None:
        """Raise unless every lane has what it needs."""
        short = self.gaps(sale_code)
        if short:
            detail = "; ".join(
                f"lane {lane} needs {', '.join(missing)}"
                for lane, missing in sorted(short.items())
            )
            raise PolicyViolation("roster.short", detail, sale=sale_code, lanes=len(short))

    def duplicate_check(self, sale_code: str) -> list[str]:
        """Anybody somehow on two lanes, for a nightly sanity run."""
        seen: dict[str, str] = {}
        clashes: list[str] = []
        for lane in self.sales.lanes(sale_code):
            for member in self.on_lane(sale_code, lane.letter):
                if not member.role.works_a_lane:
                    continue
                if member.staff_id in seen and seen[member.staff_id] != lane.letter:
                    clashes.append(
                        f"{member.staff_id} is on {seen[member.staff_id]} and {lane.letter}"
                    )
                seen[member.staff_id] = lane.letter
        return clashes
