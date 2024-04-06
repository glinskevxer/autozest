"""The arbitration desk.

Gathering the context is most of the work, and it reaches into three
subsystems: what was announced at the block, what the run recorded, and what
the buyer has already claimed. The desk does not re-derive any of it. The
announcements are read as they were, the light is read off the run, and the
hammer is the price at the fall rather than whatever a later adjustment left
behind.

That last one is worth being explicit about. A price adjustment on one claim
lowers what the buyer pays, and if the money threshold were measured against
the adjusted figure a second claim could become inadmissible because the first
one succeeded. Admissibility is judged on the sale as it happened.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from autozest.arbitration.admissibility import ClaimContext, Ruling, admissible_classes, assess
from autozest.arbitration.claims import Claim, ClaimRepository, ClaimStatus
from autozest.arbitration.policy import ArbitrationPolicy
from autozest.block.models import Run, RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.defects import DefectClass
from autozest.errors import PolicyViolation, ValidationError
from autozest.identifiers import sequenced
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.storage.records import all_rows, insert, one, update
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar

INSPECTION_PREFIX = "P"


@dataclass(frozen=True)
class PostSaleInspection:
    """A shop check the buyer paid for after the sale."""

    id: str
    run_id: int
    ordered_at: datetime
    completed_at: datetime | None = None
    passed: bool | None = None
    findings: str | None = None

    @property
    def is_open(self) -> bool:
        return self.completed_at is None


class ArbitrationDesk:
    def __init__(
        self,
        conn: sqlite3.Connection,
        policy: ArbitrationPolicy | None = None,
        calendar: BusinessCalendar | None = None,
    ) -> None:
        self.conn = conn
        self.policy = policy or ArbitrationPolicy()
        self.calendar = calendar or DEFAULT_CALENDAR
        self.claims = ClaimRepository(conn)
        self.sales = SaleRepository(conn)
        self.consignments = ConsignmentRepository(conn)
        self.inspection = InspectionService(conn)

    def context_for(
        self, run_id: int, defect: DefectClass, at: datetime | None = None
    ) -> ClaimContext:
        """Gather everything the decision needs from the record as it stands."""
        run = self.sales.run(run_id)
        self._require_sold(run)
        assert run.hammer is not None
        check = self.inspection_for(run_id)
        return ClaimContext(
            defect=defect,
            light=run.light,
            announcements=self.inspection.announcements(run.consignment_id),
            hammer=run.hammer,
            sold_at=run.ran_at or datetime.now().astimezone(),
            claimed_at=at or datetime.now().astimezone(),
            inspection_ordered_at=check.ordered_at if check else None,
            inspection_completed_at=check.completed_at if check else None,
            settled_classes=self.claims.settled_classes(run_id),
            calendar=self.calendar,
        )

    def assess(self, run_id: int, defect: DefectClass, at: datetime | None = None) -> Ruling:
        """What would happen if this claim were made right now."""
        return assess(self.context_for(run_id, defect, at), self.policy)

    def open_classes(self, run_id: int, at: datetime | None = None) -> set[DefectClass]:
        """Everything the buyer could still bring, for the phone call."""
        context = self.context_for(run_id, DefectClass.TITLE, at)
        return admissible_classes(context, self.policy)

    def open_claim(
        self,
        run_id: int,
        claimant_id: str,
        defect: DefectClass,
        summary: str,
        *,
        claimed: Money | None = None,
        actor: str = "desk",
        at: datetime | None = None,
    ) -> Claim:
        """Take a claim, or refuse it naming the rule that refused it."""
        run = self.sales.run(run_id)
        self._require_sold(run)
        if run.buyer_id != claimant_id:
            raise PolicyViolation(
                "arb.not_the_buyer",
                "a claim can only be brought by the buyer of record",
                run=run.number,
                buyer=run.buyer_id,
                claimant=claimant_id,
            )
        ruling = assess(self.context_for(run_id, defect, at), self.policy)
        if not ruling.admissible:
            raise PolicyViolation(
                ruling.rule or "arb.refused",
                ruling.detail or "the claim is not heard",
                run=run.number,
                defect=defect.value,
            )
        when = (at or datetime.now().astimezone()).date()
        claim = Claim(
            id=self.claims.next_id(when),
            run_id=run_id,
            claimant_id=claimant_id,
            defect=defect,
            summary=summary,
            policy_version=self.policy.version,
            claimed=claimed,
        )
        opened = self.claims.add(claim, actor)
        if ruling.granted_by_guarantee:
            self.claims.record(opened.id, "note", actor, "heard on the seller's guarantee")
        return opened

    def take_up(self, claim_reference: str, actor: str, note: str = "") -> Claim:
        """Move a claim into review, which is what the desk does first."""
        return self.claims.move(claim_reference, ClaimStatus.UNDER_REVIEW, actor, note)

    def withdraw(self, claim_reference: str, actor: str, reason: str) -> Claim:
        if not reason.strip():
            raise ValidationError("a withdrawal needs a reason")
        return self.claims.move(claim_reference, ClaimStatus.WITHDRAWN, actor, reason)

    def order_inspection(
        self, run_id: int, *, at: datetime | None = None
    ) -> PostSaleInspection:
        """Order a shop check, which stops the mechanical clock."""
        run = self.sales.run(run_id)
        self._require_sold(run)
        if self.inspection_for(run_id) is not None:
            raise PolicyViolation(
                "psi.already_ordered",
                "there is already an inspection on this unit",
                run=run.number,
            )
        when = at or datetime.now().astimezone()
        reference = sequenced(
            INSPECTION_PREFIX, when.date(), self._next_inspection(when.date())
        )
        insert(
            self.conn,
            "post_sale_inspection",
            {
                "id": reference,
                "run_id": run_id,
                "ordered_at": when.isoformat(timespec="seconds"),
            },
        )
        found = self.inspection_for(run_id)
        assert found is not None
        return found

    def _next_inspection(self, day: date) -> int:
        rows = all_rows(
            self.conn,
            "SELECT id FROM post_sale_inspection WHERE id LIKE ?",
            (f"{INSPECTION_PREFIX}-{day.year}-%",),
        )
        return len(rows) + 1

    def complete_inspection(
        self,
        run_id: int,
        *,
        passed: bool,
        findings: str | None = None,
        at: datetime | None = None,
    ) -> PostSaleInspection:
        check = self.inspection_for(run_id)
        if check is None:
            raise PolicyViolation(
                "psi.not_ordered", "no inspection was ordered on this unit", run=run_id
            )
        if not check.is_open:
            raise PolicyViolation(
                "psi.already_done", "that inspection is finished", inspection=check.id
            )
        update(
            self.conn,
            "post_sale_inspection",
            {"id": check.id},
            {
                "completed_at": (at or datetime.now().astimezone()).isoformat(
                    timespec="seconds"
                ),
                "passed": int(passed),
                "findings": findings,
            },
        )
        found = self.inspection_for(run_id)
        assert found is not None
        return found

    def inspection_for(self, run_id: int) -> PostSaleInspection | None:
        row = one(self.conn, "SELECT * FROM post_sale_inspection WHERE run_id = ?", (run_id,))
        if row is None:
            return None
        return PostSaleInspection(
            id=row["id"],
            run_id=row["run_id"],
            ordered_at=datetime.fromisoformat(row["ordered_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            passed=None if row["passed"] is None else bool(row["passed"]),
            findings=row["findings"],
        )

    def _require_sold(self, run: Run) -> None:
        if run.status is not RunStatus.SOLD:
            raise PolicyViolation(
                "arb.not_sold",
                "there is nothing to arbitrate on a unit that did not sell",
                run=run.number,
                status=run.status.value,
            )

    def note(self, claim_reference: str, actor: str, detail: str) -> None:
        """Put something on a claim's record without moving it."""
        self.claims.get(claim_reference)
        self.claims.record(claim_reference, "note", actor, detail)

    def queue(self) -> list[Claim]:
        return self.claims.open_claims()

    def touched_at(self, claim_reference: str) -> datetime | None:
        events = self.claims.events(claim_reference)
        return events[-1].made_at if events else None
