"""The claim record and everything that happened to it.

A claim is append only in spirit. The status moves, but every move writes an
event, and the events are what the desk works from. A buyer ringing up on day
nine wants to know why it has not moved, and "under review since Tuesday, chased
the seller on Thursday" is an answer. A status column on its own is not.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.defects import DefectClass
from autozest.errors import StateError, ValidationError
from autozest.identifiers import claim_id, parse_sequenced
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, one_or_raise, scalar, update


class ClaimStatus(Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"

    @property
    def is_finished(self) -> bool:
        return self in (ClaimStatus.RESOLVED, ClaimStatus.DENIED, ClaimStatus.WITHDRAWN)

    @property
    def counts_as_decided(self) -> bool:
        """A withdrawn claim does not bar a later one. A decided one does."""
        return self in (ClaimStatus.RESOLVED, ClaimStatus.DENIED)


class Resolution(Enum):
    BUY_BACK = "buy_back"
    PRICE_ADJUSTMENT = "price_adjustment"
    REPAIR_ALLOWANCE = "repair_allowance"
    SPLIT = "split"
    DENIED = "denied"

    @property
    def unwinds_the_sale(self) -> bool:
        return self is Resolution.BUY_BACK

    @property
    def moves_money(self) -> bool:
        return self in (
            Resolution.PRICE_ADJUSTMENT,
            Resolution.REPAIR_ALLOWANCE,
            Resolution.SPLIT,
            Resolution.BUY_BACK,
        )

    @property
    def changes_the_hammer(self) -> bool:
        """Only an adjustment restates the price the fees were charged on."""
        return self in (Resolution.PRICE_ADJUSTMENT, Resolution.SPLIT)


CLAIM_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.OPEN: frozenset(
        {ClaimStatus.UNDER_REVIEW, ClaimStatus.DENIED, ClaimStatus.WITHDRAWN}
    ),
    ClaimStatus.UNDER_REVIEW: frozenset(
        {ClaimStatus.RESOLVED, ClaimStatus.DENIED, ClaimStatus.WITHDRAWN}
    ),
    ClaimStatus.RESOLVED: frozenset(),
    ClaimStatus.DENIED: frozenset(),
    ClaimStatus.WITHDRAWN: frozenset(),
}


@dataclass(frozen=True)
class Claim:
    """One buyer's complaint about one unit."""

    id: str
    run_id: int
    claimant_id: str
    defect: DefectClass
    summary: str
    policy_version: str
    claimed: Money | None = None
    status: ClaimStatus = ClaimStatus.OPEN
    resolution: Resolution | None = None
    outcome: Money | None = None
    reason: str | None = None
    opened_at: datetime | None = None
    decided_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValidationError("a claim needs a description of the complaint")
        if self.status is ClaimStatus.RESOLVED and self.resolution is None:
            raise ValidationError("a resolved claim needs a resolution")

    def with_status(self, status: ClaimStatus) -> Claim:
        return self.decided_as(status, self.resolution, self.outcome)

    def decided_as(
        self, status: ClaimStatus, resolution: Resolution | None, outcome: Money | None
    ) -> Claim:
        """Move the claim and record its decision in the same step.

        Same reason the run does it this way: moving first builds a resolved
        claim with no resolution on it, which the constructor rejects.
        """
        if status not in CLAIM_TRANSITIONS[self.status]:
            allowed = (
                ", ".join(sorted(s.value for s in CLAIM_TRANSITIONS[self.status])) or "nothing"
            )
            raise StateError(f"claim {self.id}", self.status.value, allowed)
        return replace(self, status=status, resolution=resolution, outcome=outcome)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Claim:
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            claimant_id=row["claimant_id"],
            defect=DefectClass(row["defect_class"]),
            summary=row["summary"],
            policy_version=row["policy_version"],
            claimed=Money(row["claimed_cents"]) if row["claimed_cents"] is not None else None,
            status=ClaimStatus(row["status"]),
            resolution=Resolution(row["resolution"]) if row["resolution"] else None,
            outcome=Money(row["outcome_cents"]) if row["outcome_cents"] is not None else None,
            reason=row["reason"],
            opened_at=datetime.fromisoformat(row["opened_at"]),
            decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        )


@dataclass(frozen=True)
class ClaimEvent:
    """One line of a claim's history."""

    claim_id: str
    kind: str
    actor: str
    detail: str | None = None
    made_at: datetime | None = None


class ClaimRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def next_id(self, when: date | None = None) -> str:
        day = when or date.today()
        rows = all_rows(
            self.conn, "SELECT id FROM arbitration_claim WHERE id LIKE ?", (f"A-{day.year}-%",)
        )
        highest = max((parse_sequenced(row["id"])[2] for row in rows), default=0)
        return claim_id(day, highest + 1)

    def add(self, claim: Claim, actor: str) -> Claim:
        insert(
            self.conn,
            "arbitration_claim",
            {
                "id": claim.id,
                "run_id": claim.run_id,
                "claimant_id": claim.claimant_id,
                "defect_class": claim.defect.value,
                "summary": claim.summary,
                "claimed_cents": claim.claimed.cents if claim.claimed else None,
                "status": claim.status.value,
                "policy_version": claim.policy_version,
                "opened_at": now_text(),
            },
        )
        self.record(claim.id, "opened", actor, claim.summary)
        return self.get(claim.id)

    def get(self, claim_reference: str) -> Claim:
        row = one_or_raise(
            self.conn,
            "SELECT * FROM arbitration_claim WHERE id = ?",
            (claim_reference,),
            "claim",
            claim_reference,
        )
        return Claim.from_row(row)

    def for_run(self, run_id: int) -> list[Claim]:
        rows = all_rows(
            self.conn, "SELECT * FROM arbitration_claim WHERE run_id = ? ORDER BY id", (run_id,)
        )
        return [Claim.from_row(row) for row in rows]

    def settled_classes(self, run_id: int) -> frozenset[DefectClass]:
        """Classes already decided on this unit, which bar a second attempt.

        A withdrawn claim is not a decision and is deliberately not counted. A
        buyer who pulls a claim because the seller offered to sort it privately
        has not used up their one go.
        """
        return frozenset(
            claim.defect for claim in self.for_run(run_id) if claim.status.counts_as_decided
        )

    def for_claimant(self, claimant_id: str, status: ClaimStatus | None = None) -> list[Claim]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM arbitration_claim WHERE claimant_id = ? "
            "AND (? IS NULL OR status = ?) ORDER BY opened_at DESC",
            (claimant_id, status.value if status else None, status.value if status else None),
        )
        return [Claim.from_row(row) for row in rows]

    def open_claims(self) -> list[Claim]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM arbitration_claim WHERE status IN ('open', 'under_review') "
            "ORDER BY opened_at",
        )
        return [Claim.from_row(row) for row in rows]

    def move(
        self, claim_reference: str, status: ClaimStatus, actor: str, note: str = ""
    ) -> Claim:
        claim = self.get(claim_reference).with_status(status)
        update(
            self.conn,
            "arbitration_claim",
            {"id": claim_reference},
            {"status": claim.status.value},
        )
        self.record(claim_reference, status.value, actor, note or None)
        return self.get(claim_reference)

    def record(
        self, claim_reference: str, kind: str, actor: str, detail: str | None = None
    ) -> None:
        insert(
            self.conn,
            "claim_event",
            {
                "claim_id": claim_reference,
                "kind": kind,
                "detail": detail,
                "actor": actor,
                "made_at": now_text(),
            },
        )

    def events(self, claim_reference: str) -> list[ClaimEvent]:
        rows = all_rows(
            self.conn,
            "SELECT claim_id, kind, detail, actor, made_at FROM claim_event "
            "WHERE claim_id = ? ORDER BY id",
            (claim_reference,),
        )
        return [
            ClaimEvent(
                claim_id=row["claim_id"],
                kind=row["kind"],
                actor=row["actor"],
                detail=row["detail"],
                made_at=datetime.fromisoformat(row["made_at"]),
            )
            for row in rows
        ]

    def count_open(self) -> int:
        return int(
            scalar(
                self.conn,
                "SELECT COUNT(*) FROM arbitration_claim "
                "WHERE status IN ('open', 'under_review')",
            )
            or 0
        )
