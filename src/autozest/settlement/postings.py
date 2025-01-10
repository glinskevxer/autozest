"""Turning a sale into ledger entries.

One sold unit produces one entry with four kinds of line, and it is worth
setting out what each one is for because getting this wrong is the difference
between a seller being paid and a seller ringing up.

* the **hammer** moves from the buyer to the seller
* the **buyer fee** is charged on top of the hammer, so the buyer owes more
  than they bid
* the **seller fee** comes out of the hammer, so the seller is paid less than
  it made
* both fees are income to the auction

Services are posted separately as they are ordered, because they happen at
different times and half of them are ordered after the sale.

The posting reads the **effective** hammer, which is the price after any
arbitration adjustment. That means a sale posted before a claim is decided and
then adjusted has to be reposted, and reposting is a reversal plus a fresh
entry rather than an edit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from autozest.arbitration.claims import Resolution
from autozest.arbitration.resolution import ResolutionService
from autozest.block.models import Run, RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import Conflict, PolicyViolation
from autozest.money import Money
from autozest.settlement.fees import FeeSchedule, Service
from autozest.settlement.ledger import Account, Entry, Ledger, credit, debit
from autozest.storage.retry import writing

SOURCE_RUN = "run"
SOURCE_SERVICE = "service"
SOURCE_ARBITRATION = "arbitration"


@dataclass(frozen=True)
class SaleFigures:
    """What a sold unit costs and earns, before anything is written."""

    run: Run
    seller_id: str
    buyer_id: str
    hammer: Money
    buyer_fee: Money
    seller_fee: Money

    @property
    def buyer_owes(self) -> Money:
        return self.hammer + self.buyer_fee

    @property
    def seller_earns(self) -> Money:
        return self.hammer - self.seller_fee

    @property
    def auction_earns(self) -> Money:
        return self.buyer_fee + self.seller_fee


class PostingService:
    def __init__(self, conn: sqlite3.Connection, schedule: FeeSchedule | None = None) -> None:
        self.conn = conn
        self.schedule = schedule or FeeSchedule()
        self.ledger = Ledger(conn)
        self.sales = SaleRepository(conn)
        self.consignments = ConsignmentRepository(conn)
        self.resolutions = ResolutionService(conn)

    def figures(self, run_id: int) -> SaleFigures:
        """Work out the money on a sold unit without writing anything."""
        run = self.sales.run(run_id)
        if run.status is not RunStatus.SOLD or run.buyer_id is None:
            raise PolicyViolation(
                "settle.not_sold",
                "there is nothing to settle on a unit that did not sell",
                run=run.number,
                status=run.status.value,
            )
        hammer = self.resolutions.effective_hammer(run_id)
        consignment = self.consignments.get(run.consignment_id)
        return SaleFigures(
            run=run,
            seller_id=consignment.seller_id,
            buyer_id=run.buyer_id,
            hammer=hammer,
            buyer_fee=self.schedule.buyer_fee(hammer),
            seller_fee=self.schedule.seller_fee(hammer),
        )

    def post_sale(self, run_id: int) -> Entry:
        """Write the sale to the ledger. Once per run."""
        if self.ledger.entries_for(SOURCE_RUN, str(run_id)):
            raise Conflict("that run is already posted", run=run_id)
        figures = self.figures(run_id)
        return self.ledger.post(self._sale_entry(figures))

    def post_sale_batch(self, run_ids: list[int]) -> list[Entry]:
        """Post a whole sale's worth in one transaction.

        Settling three hundred runs one commit at a time is three hundred
        fsyncs and, on the night the office machine died half way through, a
        ledger that balanced and a sale that was half posted. Either all of
        them land or none of them do.
        """
        posted: list[Entry] = []
        with writing(self.conn):
            for run_id in run_ids:
                if self.is_posted(run_id):
                    continue
                posted.append(self.post_sale(run_id))
        return posted

    def _sale_entry(self, figures: SaleFigures) -> Entry:
        return Entry(
            reference=f"{figures.run.sale_code}/{figures.run.number}",
            memo=f"sale of {figures.run.consignment_id}",
            source=SOURCE_RUN,
            source_id=str(figures.run.id),
            lines=(
                debit(Account.BUYER_RECEIVABLE, figures.buyer_owes, figures.buyer_id),
                credit(Account.SELLER_PAYABLE, figures.seller_earns, figures.seller_id),
                credit(Account.FEE_INCOME, figures.auction_earns, memo="buyer and seller fee"),
            ),
        )

    def repost_sale(self, run_id: int, reason: str) -> Entry:
        """Reverse the posted sale and write it again at the current figures.

        This is what an arbitration adjustment needs. The fee is chosen by band
        and an adjustment can cross a band, so recomputing the difference is
        not enough. The whole entry goes and a fresh one replaces it.
        """
        existing = self.ledger.entries_for(SOURCE_RUN, str(run_id))
        if not existing:
            raise PolicyViolation("settle.not_posted", "that run was never posted", run=run_id)
        live = [entry for entry in existing if entry.memo.startswith("sale of")]
        for entry in live:
            assert entry.id is not None
            self.ledger.reverse(entry.id, reason)
        return self.ledger.post(self._sale_entry(self.figures(run_id)))

    def post_service(
        self,
        run_id: int,
        service: Service,
        *,
        party_id: str | None = None,
        amount: Money | None = None,
    ) -> Entry:
        """Charge a service to whichever side it falls on."""
        run = self.sales.run(run_id)
        consignment = self.consignments.get(run.consignment_id)
        charged = amount if amount is not None else self.schedule.service(service)
        if service.falls_on_buyer:
            if run.buyer_id is None:
                raise PolicyViolation(
                    "settle.no_buyer",
                    f"{service.value} falls on the buyer and this unit has none",
                    run=run.number,
                )
            payer = party_id or run.buyer_id
            owed_line = debit(Account.BUYER_RECEIVABLE, charged, payer)
        else:
            payer = party_id or consignment.seller_id
            owed_line = debit(Account.SELLER_PAYABLE, charged, payer)
        return self.ledger.post(
            Entry(
                reference=f"{run.sale_code}/{run.number}",
                memo=f"{service.value} on {run.consignment_id}",
                source=SOURCE_SERVICE,
                source_id=f"{run_id}:{service.value}",
                lines=(
                    owed_line,
                    credit(Account.SERVICE_INCOME, charged, memo=service.value),
                ),
            )
        )

    def post_arbitration(self, claim_reference: str) -> Entry:
        """Write what an arbitration decision moves.

        An adjustment does not post money of its own. It changes the price the
        sale was posted at, so the sale is reposted instead and this returns
        that entry.
        """
        claim = self.resolutions.claims.get(claim_reference)
        if claim.resolution is None or not claim.resolution.moves_money:
            raise PolicyViolation(
                "settle.nothing_to_post",
                "that decision does not move any money",
                claim=claim_reference,
            )
        if claim.resolution.changes_the_hammer:
            return self.repost_sale(claim.run_id, f"arbitration {claim_reference}")

        outcome = self.resolutions.plan(claim_reference, claim.resolution, claim.outcome)
        run = self.sales.run(claim.run_id)
        consignment = self.consignments.get(run.consignment_id)
        assert run.buyer_id is not None
        lines = [
            credit(Account.BUYER_RECEIVABLE, outcome.buyer_receives, run.buyer_id),
            debit(Account.SELLER_PAYABLE, outcome.seller_pays, consignment.seller_id),
        ]
        if outcome.auction_pays:
            lines.append(debit(Account.ARBITRATION_EXPENSE, outcome.auction_pays))
        if claim.resolution is Resolution.BUY_BACK:
            lines = [
                credit(Account.BUYER_RECEIVABLE, outcome.buyer_receives, run.buyer_id),
                debit(Account.SELLER_PAYABLE, outcome.seller_pays, consignment.seller_id),
            ]
        return self.ledger.post(
            Entry(
                reference=f"{run.sale_code}/{run.number}",
                memo=f"{claim.resolution.value} on {claim_reference}",
                source=SOURCE_ARBITRATION,
                source_id=claim_reference,
                lines=tuple(lines),
            )
        )

    def is_posted(self, run_id: int) -> bool:
        return bool(self.ledger.entries_for(SOURCE_RUN, str(run_id)))
