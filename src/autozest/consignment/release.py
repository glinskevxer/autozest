"""Keys, and letting a unit off the site.

Two records, and they are separate on purpose. Keys are handed over at the
cabinet, often the day before. The gate release is what the guard on the barrier
acts on, and it is the record that gets pulled when a unit leaves that should
not have.

Release is refused while anything is holding the unit, and the holds it asks
about are the settlement ones: has the buyer paid, is the title here, is an
arbitration open. A supervisor can override, and the override is recorded with
the reasons it went past. An override that is not written down is the same as
no rule at all.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from autozest.identifiers import sequenced
from autozest.settlement.proceeds import ProceedsService
from autozest.storage.records import all_rows, insert, now_text, one, scalar, update

RELEASE_PREFIX = "G"


@dataclass(frozen=True)
class KeySet:
    """The keys for one unit, in a cabinet, on a tag."""

    consignment_id: str
    tag: str
    count: int
    location: str
    held_since: datetime
    handed_to: str | None = None
    handed_at: datetime | None = None

    @property
    def is_held(self) -> bool:
        return self.handed_at is None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> KeySet:
        return cls(
            consignment_id=row["consignment_id"],
            tag=row["tag"],
            count=row["count"],
            location=row["location"],
            held_since=datetime.fromisoformat(row["held_since"]),
            handed_to=row["handed_to"],
            handed_at=(datetime.fromisoformat(row["handed_at"]) if row["handed_at"] else None),
        )


@dataclass(frozen=True)
class GateRelease:
    """The authority a guard acts on."""

    id: str
    consignment_id: str
    released_to: str
    authorised_by: str
    released_at: datetime
    holds_overridden: str | None = None

    @property
    def was_overridden(self) -> bool:
        return bool(self.holds_overridden)


class ReleaseService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.consignments = ConsignmentRepository(conn)
        self.proceeds = ProceedsService(conn)

    def take_keys(
        self, consignment_id: str, tag: str, count: int, location: str = "cabinet"
    ) -> KeySet:
        """Log a set of keys into the cabinet."""
        self.consignments.get(consignment_id)
        if count < 0:
            raise ValidationError("a negative key count is not a thing")
        insert(
            self.conn,
            "key_set",
            {
                "consignment_id": consignment_id,
                "tag": tag,
                "count": count,
                "location": location,
                "held_since": now_text(),
            },
        )
        return self.keys(consignment_id)

    def keys(self, consignment_id: str) -> KeySet:
        row = one(
            self.conn, "SELECT * FROM key_set WHERE consignment_id = ?", (consignment_id,)
        )
        if row is None:
            raise NotFound("key set", consignment_id)
        return KeySet.from_row(row)

    def by_tag(self, tag: str) -> KeySet:
        row = one(self.conn, "SELECT * FROM key_set WHERE tag = ?", (tag,))
        if row is None:
            raise NotFound("key tag", tag)
        return KeySet.from_row(row)

    def hand_over(self, consignment_id: str, to: str) -> KeySet:
        """Hand the keys across. Once."""
        keys = self.keys(consignment_id)
        if not keys.is_held:
            raise PolicyViolation(
                "keys.already_handed",
                f"those keys went to {keys.handed_to}",
                consignment=consignment_id,
                tag=keys.tag,
            )
        update(
            self.conn,
            "key_set",
            {"consignment_id": consignment_id},
            {"handed_to": to, "handed_at": now_text()},
        )
        return self.keys(consignment_id)

    def held(self) -> list[KeySet]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM key_set WHERE handed_at IS NULL ORDER BY tag",
        )
        return [KeySet.from_row(row) for row in rows]

    def release(
        self,
        consignment_id: str,
        released_to: str,
        authorised_by: str,
        *,
        override: str | None = None,
    ) -> GateRelease:
        """Let a unit off the site.

        Refused while settlement is holding it, unless a supervisor overrides
        and says why. An override nobody wrote down is the same as no rule.
        """
        self.consignments.get(consignment_id)
        if self.find_release(consignment_id) is not None:
            raise Conflict("that unit has already been released", consignment=consignment_id)
        holds = [hold.detail for hold in self.proceeds.position(consignment_id).holds]
        if holds and override is None:
            raise PolicyViolation(
                "release.held",
                "; ".join(holds),
                consignment=consignment_id,
                holds=len(holds),
            )
        reference = sequenced(RELEASE_PREFIX, date.today(), self._next())
        insert(
            self.conn,
            "gate_release",
            {
                "id": reference,
                "consignment_id": consignment_id,
                "released_to": released_to,
                "authorised_by": authorised_by,
                "holds_overridden": (
                    f"{override}: {'; '.join(holds)}" if holds and override else None
                ),
                "released_at": now_text(),
            },
        )
        return self.get_release(reference)

    def _next(self) -> int:
        return int(scalar(self.conn, "SELECT COUNT(*) FROM gate_release") or 0) + 1

    def get_release(self, reference: str) -> GateRelease:
        row = one(self.conn, "SELECT * FROM gate_release WHERE id = ?", (reference,))
        if row is None:
            raise NotFound("gate release", reference)
        return self._release(row)

    def find_release(self, consignment_id: str) -> GateRelease | None:
        row = one(
            self.conn,
            "SELECT * FROM gate_release WHERE consignment_id = ?",
            (consignment_id,),
        )
        return self._release(row) if row else None

    @staticmethod
    def _release(row: dict[str, Any]) -> GateRelease:
        return GateRelease(
            id=row["id"],
            consignment_id=row["consignment_id"],
            released_to=row["released_to"],
            authorised_by=row["authorised_by"],
            holds_overridden=row["holds_overridden"],
            released_at=datetime.fromisoformat(row["released_at"]),
        )

    def overrides(self) -> list[GateRelease]:
        """Every unit that went out past a hold, for the morning review."""
        rows = all_rows(
            self.conn,
            "SELECT * FROM gate_release WHERE holds_overridden IS NOT NULL ORDER BY id",
        )
        return [self._release(row) for row in rows]
