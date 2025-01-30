"""Working out what needs saying, and queueing it.

Run nightly. It looks at the state of the building and queues the letters that
state implies: titles we are still waiting on, claims that were decided today,
buyers whose money is late.

It is idempotent, which is the only property that matters. Running it twice
must not send a seller two chase letters, and a nightly job that anybody can
rerun after a failure is worth far more than one that has to be run exactly
once. So every notice carries a reference, and a notice with that reference
already on the queue means there is nothing to do.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from autozest.arbitration.claims import ClaimRepository, ClaimStatus
from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.money import Money
from autozest.notices import Channel, NoticeQueue, claim_decided, payment_due, title_chase
from autozest.settlement.payments import PaymentService
from autozest.settlement.statements import StatementService
from autozest.storage.records import all_rows
from autozest.title.service import TitleService

#: How long a title can be outstanding before the seller hears from us.
CHASE_AFTER_DAYS = 14

#: How often we chase again after that.
CHASE_EVERY_DAYS = 7


@dataclass(frozen=True)
class Dispatch:
    """What one run of the job queued."""

    queued: int
    already_there: int
    kinds: dict[str, int]

    def summary(self) -> str:
        return f"{self.queued} queued, {self.already_there} already on the queue"


class DispatchService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.notices = NoticeQueue(conn)
        self.titles = TitleService(conn)
        self.claims = ClaimRepository(conn)
        self.sales = SaleRepository(conn)
        self.consignments = ConsignmentRepository(conn)
        self.statements = StatementService(conn)
        self.payments = PaymentService(conn)

    def already_queued(self, reference: str) -> bool:
        rows = all_rows(
            self.conn,
            "SELECT 1 FROM notice WHERE reference = ? AND status != 'cancelled' LIMIT 1",
            (reference,),
        )
        return bool(rows)

    def run(self, *, as_at: date | None = None) -> Dispatch:
        """Queue everything today's state implies."""
        today = as_at or date.today()
        queued = 0
        skipped = 0
        kinds: dict[str, int] = {}

        for kind, party, subject, body, reference, channel in self._wanted(today):
            if self.already_queued(reference):
                skipped += 1
                continue
            self.notices.queue(kind, party, subject, body, channel=channel, reference=reference)
            queued += 1
            kinds[kind] = kinds.get(kind, 0) + 1
        return Dispatch(queued=queued, already_there=skipped, kinds=kinds)

    def _wanted(self, today: date) -> list[tuple[str, str, str, str, str, Channel]]:
        wanted: list[tuple[str, str, str, str, str, Channel]] = []
        wanted.extend(self._title_chases(today))
        wanted.extend(self._claim_decisions())
        wanted.extend(self._payments_due(today))
        return wanted

    def _title_chases(self, today: date) -> list[tuple[str, str, str, str, str, Channel]]:
        found: list[tuple[str, str, str, str, str, Channel]] = []
        for record in self.titles.outstanding():
            consignment = self.consignments.get(record.consignment_id)
            run = self.sales.run_for_consignment(record.consignment_id)
            if run is None or run.status is not RunStatus.SOLD:
                continue
            sold_on = self.sales.get(run.sale_code).sale_date
            waiting = (today - sold_on).days
            if waiting < CHASE_AFTER_DAYS:
                continue
            batch = (waiting - CHASE_AFTER_DAYS) // CHASE_EVERY_DAYS
            subject, body = title_chase(record.consignment_id, waiting)
            found.append(
                (
                    "title.chase",
                    consignment.seller_id,
                    subject,
                    body,
                    f"title:{record.consignment_id}:{batch}",
                    Channel.EMAIL,
                )
            )
        return found

    def _claim_decisions(self) -> list[tuple[str, str, str, str, str, Channel]]:
        found: list[tuple[str, str, str, str, str, Channel]] = []
        rows = all_rows(
            self.conn,
            "SELECT id FROM arbitration_claim WHERE status IN ('resolved', 'denied')",
        )
        for row in rows:
            claim = self.claims.get(row["id"])
            if claim.status is ClaimStatus.WITHDRAWN:
                continue
            subject, body = claim_decided(
                claim.id,
                claim.resolution.value if claim.resolution else "decided",
                claim.outcome or Money.zero(),
            )
            found.append(
                (
                    "claim.decided",
                    claim.claimant_id,
                    subject,
                    body,
                    f"claim:{claim.id}",
                    Channel.EMAIL,
                )
            )
        return found

    def _payments_due(self, today: date) -> list[tuple[str, str, str, str, str, Channel]]:
        found: list[tuple[str, str, str, str, str, Channel]] = []
        for party, balance in self.statements.outstanding_buyers().items():
            due = self.payments.due_date(datetime.combine(today, datetime.min.time()))
            subject, body = payment_due(balance, due.date())
            found.append(
                (
                    "payment.due",
                    party,
                    subject,
                    body,
                    f"payment:{party}:{today.isoformat()}",
                    Channel.EMAIL,
                )
            )
        return found

    def next_chase(self, consignment_id: str, sold_on: date) -> date:
        """When this title's seller hears from us next."""
        return sold_on + timedelta(days=CHASE_AFTER_DAYS)
