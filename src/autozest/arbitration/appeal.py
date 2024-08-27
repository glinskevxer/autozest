"""Appeals against a decision.

One tier and one only. A second tier sounds fairer and in practice means every
decision is appealed twice, the desk stops deciding anything properly because
it will be reviewed anyway, and a claim takes six weeks instead of four days.

Either side can appeal, which is worth saying because the obvious design lets
only the buyer. A seller handed a buy back they think is wrong has exactly as
much at stake, and a process only one side can use is not a process.

The window is short and counted in business days from the decision. An appeal
is a second look at a decision somebody already made carefully, not a way to
reopen a claim once the unit has been sitting on a forecourt for a month.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.arbitration.claims import Claim, ClaimRepository, ClaimStatus, Resolution
from autozest.arbitration.resolution import ResolutionService
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from autozest.identifiers import sequenced
from autozest.money import Money
from autozest.storage.records import all_rows, insert, one, scalar, update
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar, Window, WindowUnit

APPEAL_PREFIX = "E"

#: How long after a decision an appeal can be raised.
APPEAL_WINDOW = Window(3, WindowUnit.BUSINESS_DAYS)


class AppealStatus(Enum):
    OPEN = "open"
    UPHELD = "upheld"
    OVERTURNED = "overturned"
    VARIED = "varied"
    WITHDRAWN = "withdrawn"

    @property
    def is_decided(self) -> bool:
        return self is not AppealStatus.OPEN

    @property
    def changes_the_claim(self) -> bool:
        """Whether the original decision no longer stands as it was."""
        return self in (AppealStatus.OVERTURNED, AppealStatus.VARIED)


@dataclass(frozen=True)
class Appeal:
    """One challenge to one decision."""

    id: str
    claim_id: str
    raised_by: str
    grounds: str
    status: AppealStatus = AppealStatus.OPEN
    decided_by: str | None = None
    decision_note: str | None = None
    new_outcome: Money | None = None
    raised_at: datetime | None = None
    decided_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Appeal:
        return cls(
            id=row["id"],
            claim_id=row["claim_id"],
            raised_by=row["raised_by"],
            grounds=row["grounds"],
            status=AppealStatus(row["status"]),
            decided_by=row["decided_by"],
            decision_note=row["decision_note"],
            new_outcome=(
                Money(row["new_outcome_cents"])
                if row["new_outcome_cents"] is not None
                else None
            ),
            raised_at=datetime.fromisoformat(row["raised_at"]),
            decided_at=(
                datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None
            ),
        )


class AppealService:
    def __init__(
        self, conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
    ) -> None:
        self.conn = conn
        self.calendar = calendar or DEFAULT_CALENDAR
        self.claims = ClaimRepository(conn)
        self.resolutions = ResolutionService(conn)
        self.sales = SaleRepository(conn)
        self.consignments = ConsignmentRepository(conn)

    def parties(self, claim: Claim) -> set[str]:
        """The two accounts entitled to appeal this claim."""
        run = self.sales.run(claim.run_id)
        seller = self.consignments.get(run.consignment_id).seller_id
        return {claim.claimant_id, seller}

    def deadline(self, claim: Claim) -> datetime:
        if claim.decided_at is None:
            raise PolicyViolation(
                "appeal.not_decided",
                "that claim has not been decided yet",
                claim=claim.id,
            )
        return self.calendar.deadline(claim.decided_at, APPEAL_WINDOW)

    def raise_appeal(
        self,
        claim_reference: str,
        raised_by: str,
        grounds: str,
        *,
        at: datetime | None = None,
    ) -> Appeal:
        """Challenge a decision, if you are entitled to and it is in time."""
        if not grounds.strip():
            raise ValidationError("an appeal needs grounds")
        claim = self.claims.get(claim_reference)
        if not claim.status.counts_as_decided:
            raise PolicyViolation(
                "appeal.not_decided",
                f"that claim is {claim.status.value}",
                claim=claim_reference,
            )
        if raised_by not in self.parties(claim):
            raise PolicyViolation(
                "appeal.not_a_party",
                "only the buyer or the seller can appeal a decision",
                claim=claim_reference,
                account=raised_by,
            )
        when = at or datetime.now().astimezone()
        closes = self.deadline(claim)
        if when > closes:
            raise PolicyViolation(
                "appeal.late",
                f"the {APPEAL_WINDOW} appeal window closed at {closes}",
                claim=claim_reference,
            )
        if self.for_claim(claim_reference) is not None:
            raise Conflict("that decision has already been appealed", claim=claim_reference)
        reference = sequenced(APPEAL_PREFIX, when.date(), self._next(when.date()))
        insert(
            self.conn,
            "claim_appeal",
            {
                "id": reference,
                "claim_id": claim_reference,
                "raised_by": raised_by,
                "grounds": grounds,
                "status": AppealStatus.OPEN.value,
                "raised_at": when.isoformat(timespec="seconds"),
            },
        )
        self.claims.record(claim_reference, "appealed", raised_by, grounds)
        return self.get(reference)

    def _next(self, day: date) -> int:
        used = scalar(
            self.conn,
            "SELECT COUNT(*) FROM claim_appeal WHERE id LIKE ?",
            (f"{APPEAL_PREFIX}-{day.year}-%",),
        )
        return int(used or 0) + 1

    def get(self, reference: str) -> Appeal:
        row = one(self.conn, "SELECT * FROM claim_appeal WHERE id = ?", (reference,))
        if row is None:
            raise NotFound("appeal", reference)
        return Appeal.from_row(row)

    def for_claim(self, claim_reference: str) -> Appeal | None:
        row = one(
            self.conn, "SELECT * FROM claim_appeal WHERE claim_id = ?", (claim_reference,)
        )
        return Appeal.from_row(row) if row else None

    def open_appeals(self) -> list[Appeal]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM claim_appeal WHERE status = 'open' ORDER BY raised_at",
        )
        return [Appeal.from_row(row) for row in rows]

    def decide(
        self,
        reference: str,
        status: AppealStatus,
        *,
        decided_by: str,
        note: str,
        new_outcome: Money | None = None,
        at: datetime | None = None,
    ) -> Appeal:
        """Settle an appeal, and move the claim with it where that applies."""
        appeal = self.get(reference)
        if appeal.status.is_decided:
            raise PolicyViolation(
                "appeal.already_decided",
                f"that appeal is {appeal.status.value}",
                appeal=reference,
            )
        if status is AppealStatus.OPEN:
            raise ValidationError("an appeal cannot be decided as open")
        if status is AppealStatus.VARIED and new_outcome is None:
            raise ValidationError("a varied appeal needs the new figure")
        if not note.strip():
            raise ValidationError("an appeal decision needs a note")

        claim = self.claims.get(appeal.claim_id)
        values: dict[str, object] = {
            "status": status.value,
            "decided_by": decided_by,
            "decision_note": note,
            "decided_at": (at or datetime.now().astimezone()).isoformat(timespec="seconds"),
        }
        if new_outcome is not None:
            values["new_outcome_cents"] = new_outcome.cents
        update(self.conn, "claim_appeal", {"id": reference}, values)
        self._apply(claim, status, new_outcome, decided_by, note)
        return self.get(reference)

    def _apply(
        self,
        claim: Claim,
        status: AppealStatus,
        new_outcome: Money | None,
        decided_by: str,
        note: str,
    ) -> None:
        """Push an overturn or a variation back onto the claim record."""
        if not status.changes_the_claim:
            self.claims.record(claim.id, f"appeal {status.value}", decided_by, note)
            return
        if status is AppealStatus.OVERTURNED:
            wanted = (
                ClaimStatus.DENIED
                if claim.status is ClaimStatus.RESOLVED
                else ClaimStatus.RESOLVED
            )
            resolution = (
                Resolution.DENIED
                if wanted is ClaimStatus.DENIED
                else claim.resolution or Resolution.REPAIR_ALLOWANCE
            )
            outcome = (
                Money.zero()
                if wanted is ClaimStatus.DENIED
                else (new_outcome or claim.claimed or Money.zero())
            )
        else:
            wanted = claim.status
            resolution = claim.resolution or Resolution.REPAIR_ALLOWANCE
            outcome = new_outcome or Money.zero()
        update(
            self.conn,
            "arbitration_claim",
            {"id": claim.id},
            {
                "status": wanted.value,
                "resolution": resolution.value,
                "outcome_cents": outcome.cents,
                "reason": note,
            },
        )
        self.claims.record(claim.id, f"appeal {status.value}", decided_by, note)

    def withdraw(self, reference: str, by: str, reason: str) -> Appeal:
        return self.decide(reference, AppealStatus.WITHDRAWN, decided_by=by, note=reason)
