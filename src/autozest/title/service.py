"""Moving a title through the building.

The whole job is knowing where a piece of paper is and refusing to send it
somewhere it should not go. Three quarters of the arguments a title clerk has
are about a title that was sent to the wrong party, and every one of them is
expensive because getting it back means a state office and six weeks.

The interesting rule is what happens when a title turns up carrying a brand
nobody called. That is not a hold and it is not a late arbitration claim. It is
an automatic unwind, and the desk is told about it the moment the title is
logged rather than when someone happens to look.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.arbitration.claims import ClaimRepository, ClaimStatus
from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.models import ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import Conflict, NotFound, PolicyViolation, StateError
from autozest.inspection.service import InspectionService
from autozest.storage.records import all_rows, insert, now_text, one, one_or_raise, update
from autozest.title.branding import TitleBrand, undisclosed_brand


class TitleStatus(Enum):
    """Where the paper is."""

    EXPECTED = "expected"
    ON_HAND = "on_hand"
    ATTORNEY = "attorney"
    ABSENT = "absent"
    LOST_APPLIED = "lost_applied"
    RELEASED = "released"
    SENT = "sent"

    @property
    def is_here(self) -> bool:
        return self is TitleStatus.ON_HAND

    @property
    def has_left(self) -> bool:
        return self in (TitleStatus.SENT, TitleStatus.RELEASED)


TITLE_TRANSITIONS: dict[TitleStatus, frozenset[TitleStatus]] = {
    TitleStatus.EXPECTED: frozenset(
        {TitleStatus.ON_HAND, TitleStatus.ABSENT, TitleStatus.ATTORNEY}
    ),
    TitleStatus.ABSENT: frozenset(
        {TitleStatus.ON_HAND, TitleStatus.ATTORNEY, TitleStatus.LOST_APPLIED}
    ),
    TitleStatus.ATTORNEY: frozenset({TitleStatus.ON_HAND, TitleStatus.LOST_APPLIED}),
    TitleStatus.LOST_APPLIED: frozenset({TitleStatus.ON_HAND}),
    TitleStatus.ON_HAND: frozenset({TitleStatus.SENT, TitleStatus.RELEASED}),
    TitleStatus.SENT: frozenset(),
    TitleStatus.RELEASED: frozenset(),
}


@dataclass(frozen=True)
class TitleRecord:
    """One title, and where it is."""

    consignment_id: str
    status: TitleStatus
    brand: TitleBrand = TitleBrand.CLEAN
    issuing_state: str | None = None
    title_number: str | None = None
    received_on: date | None = None
    released_on: date | None = None
    note: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TitleRecord:
        return cls(
            consignment_id=row["consignment_id"],
            status=TitleStatus(row["status"]),
            brand=TitleBrand(row["brand"]),
            issuing_state=row["issuing_state"],
            title_number=row["title_number"],
            received_on=date.fromisoformat(row["received_on"]) if row["received_on"] else None,
            released_on=date.fromisoformat(row["released_on"]) if row["released_on"] else None,
            note=row["note"],
        )


@dataclass(frozen=True)
class Movement:
    consignment_id: str
    to_status: TitleStatus
    actor: str
    from_status: TitleStatus | None = None
    detail: str | None = None
    made_at: datetime | None = None


class TitleService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.consignments = ConsignmentRepository(conn)
        self.sales = SaleRepository(conn)
        self.claims = ClaimRepository(conn)
        self.inspection = InspectionService(conn)

    def expect(self, consignment_id: str, *, actor: str = "title") -> TitleRecord:
        """Open a title record for a unit that has been consigned."""
        self.consignments.get(consignment_id)
        if self.find(consignment_id) is not None:
            raise Conflict("that unit already has a title record", consignment=consignment_id)
        insert(
            self.conn,
            "title_record",
            {
                "consignment_id": consignment_id,
                "status": TitleStatus.EXPECTED.value,
                "brand": TitleBrand.CLEAN.value,
                "updated_at": now_text(),
            },
        )
        self._movement(consignment_id, None, TitleStatus.EXPECTED, actor, None)
        return self.get(consignment_id)

    def get(self, consignment_id: str) -> TitleRecord:
        row = one_or_raise(
            self.conn,
            "SELECT * FROM title_record WHERE consignment_id = ?",
            (consignment_id,),
            "title record",
            consignment_id,
        )
        return TitleRecord.from_row(row)

    def find(self, consignment_id: str) -> TitleRecord | None:
        row = one(
            self.conn, "SELECT * FROM title_record WHERE consignment_id = ?", (consignment_id,)
        )
        return TitleRecord.from_row(row) if row else None

    def receive(
        self,
        consignment_id: str,
        *,
        brand: TitleBrand = TitleBrand.CLEAN,
        issuing_state: str | None = None,
        title_number: str | None = None,
        on: date | None = None,
        actor: str = "title",
    ) -> tuple[TitleRecord, TitleBrand | None]:
        """Log a title in, and say straight away if it carries a surprise.

        The second half of the return is the brand nobody called. It is not an
        error, because the title still has to be recorded, but it is not
        something the clerk should have to go looking for either.
        """
        record = self.get(consignment_id)
        self._require_move(record, TitleStatus.ON_HAND)
        update(
            self.conn,
            "title_record",
            {"consignment_id": consignment_id},
            {
                "status": TitleStatus.ON_HAND.value,
                "brand": brand.value,
                "issuing_state": issuing_state,
                "title_number": title_number,
                "received_on": (on or date.today()).isoformat(),
                "updated_at": now_text(),
            },
        )
        self._movement(
            consignment_id, record.status, TitleStatus.ON_HAND, actor, f"brand {brand.value}"
        )
        surprise = self.undisclosed(consignment_id, brand)
        if surprise is not None:
            self._movement(
                consignment_id,
                TitleStatus.ON_HAND,
                TitleStatus.ON_HAND,
                actor,
                f"undisclosed {surprise.value} brand, the sale unwinds",
            )
        return self.get(consignment_id), surprise

    def undisclosed(self, consignment_id: str, brand: TitleBrand) -> TitleBrand | None:
        """The brand that should have been called at the block and was not."""
        return undisclosed_brand(brand, self.inspection.announcements(consignment_id))

    def mark(
        self,
        consignment_id: str,
        status: TitleStatus,
        *,
        actor: str = "title",
        detail: str | None = None,
    ) -> TitleRecord:
        """Move a title record without receiving or sending anything."""
        record = self.get(consignment_id)
        self._require_move(record, status)
        update(
            self.conn,
            "title_record",
            {"consignment_id": consignment_id},
            {"status": status.value, "updated_at": now_text(), "note": detail},
        )
        self._movement(consignment_id, record.status, status, actor, detail)
        return self.get(consignment_id)

    def holds(self, consignment_id: str) -> list[str]:
        """Every reason this title cannot go to the buyer yet."""
        record = self.get(consignment_id)
        reasons: list[str] = []
        if not record.status.is_here:
            reasons.append(f"the title is {record.status.value}")
        consignment = self.consignments.get(consignment_id)
        if consignment.status is not ConsignmentStatus.SOLD:
            reasons.append("the unit has not sold")
        run = self.sales.run_for_consignment(consignment_id)
        if run is not None and run.status is RunStatus.SOLD:
            open_claims = [
                claim
                for claim in self.claims.for_run(run.id or 0)
                if claim.status in (ClaimStatus.OPEN, ClaimStatus.UNDER_REVIEW)
            ]
            if open_claims:
                reasons.append(f"{len(open_claims)} arbitration claims are open")
        return reasons

    def send(self, consignment_id: str, *, actor: str = "title") -> TitleRecord:
        """Send the title to the buyer, if nothing is holding it."""
        reasons = self.holds(consignment_id)
        if reasons:
            raise PolicyViolation(
                "title.held",
                "; ".join(reasons),
                consignment=consignment_id,
                holds=len(reasons),
            )
        record = self.mark(consignment_id, TitleStatus.SENT, actor=actor, detail="to the buyer")
        update(
            self.conn,
            "title_record",
            {"consignment_id": consignment_id},
            {"released_on": date.today().isoformat()},
        )
        return record

    def release_to_seller(self, consignment_id: str, *, actor: str = "title") -> TitleRecord:
        """Give the title back on a unit that did not sell."""
        consignment = self.consignments.get(consignment_id)
        if consignment.status is ConsignmentStatus.SOLD:
            raise PolicyViolation(
                "title.sold",
                "that unit sold, its title goes to the buyer",
                consignment=consignment_id,
            )
        return self.mark(
            consignment_id, TitleStatus.RELEASED, actor=actor, detail="back to the seller"
        )

    def outstanding(self) -> list[TitleRecord]:
        """Every title we are still waiting on."""
        rows = all_rows(
            self.conn,
            "SELECT * FROM title_record WHERE status IN "
            "('expected', 'absent', 'attorney', 'lost_applied') ORDER BY consignment_id",
        )
        return [TitleRecord.from_row(row) for row in rows]

    def movements(self, consignment_id: str) -> list[Movement]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM title_movement WHERE consignment_id = ? ORDER BY id",
            (consignment_id,),
        )
        return [
            Movement(
                consignment_id=row["consignment_id"],
                from_status=TitleStatus(row["from_status"]) if row["from_status"] else None,
                to_status=TitleStatus(row["to_status"]),
                actor=row["actor"],
                detail=row["detail"],
                made_at=datetime.fromisoformat(row["made_at"]),
            )
            for row in rows
        ]

    def require(self, consignment_id: str) -> TitleRecord:
        record = self.find(consignment_id)
        if record is None:
            raise NotFound("title record", consignment_id)
        return record

    def _require_move(self, record: TitleRecord, wanted: TitleStatus) -> None:
        if wanted not in TITLE_TRANSITIONS[record.status]:
            allowed = (
                ", ".join(sorted(s.value for s in TITLE_TRANSITIONS[record.status]))
                or "nothing"
            )
            raise StateError(
                f"the title for {record.consignment_id}", record.status.value, allowed
            )

    def _movement(
        self,
        consignment_id: str,
        from_status: TitleStatus | None,
        to_status: TitleStatus,
        actor: str,
        detail: str | None,
    ) -> None:
        insert(
            self.conn,
            "title_movement",
            {
                "consignment_id": consignment_id,
                "from_status": from_status.value if from_status else None,
                "to_status": to_status.value,
                "actor": actor,
                "detail": detail,
                "made_at": now_text(),
            },
        )
