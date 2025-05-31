"""When a seller gets paid.

This is the one place in the system where three subsystems that know nothing
about each other have to agree before anything happens. The title desk owns
whether the paperwork is here. The arbitration desk owns whether the buyer is
still arguing. Settlement owns whether the money has actually arrived. None of
them can release proceeds on their own and none of them should have to know
about the others, so the question is asked here.

The order the holds are listed in is the order they are usually cleared in,
because a seller ringing up wants to know what is next rather than a set.

A lien payoff is not a hold. It comes out of the proceeds before the seller
sees them, which is a different thing: the money moves, it just does not all
move to the seller.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from autozest.arbitration.claims import ClaimRepository, ClaimStatus
from autozest.audit import AuditLog
from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.models import ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import PolicyViolation
from autozest.money import Money
from autozest.settlement.ledger import Account, Entry, Ledger, credit, debit
from autozest.settlement.payments import Method, PaymentService
from autozest.settlement.statements import StatementService
from autozest.title.service import TitleService, TitleStatus


@dataclass(frozen=True)
class Hold:
    """One reason the money is staying where it is."""

    code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class ProceedsPosition:
    """Everything about one unit's money, in one answer."""

    consignment_id: str
    seller_id: str
    gross: Money
    lien: Money
    holds: tuple[Hold, ...]

    @property
    def is_clear(self) -> bool:
        return not self.holds

    @property
    def net(self) -> Money:
        """What the seller is handed once the lien is paid off."""
        return self.gross - self.lien

    @property
    def next_hold(self) -> Hold | None:
        return self.holds[0] if self.holds else None


class ProceedsService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.consignments = ConsignmentRepository(conn)
        self.sales = SaleRepository(conn)
        self.claims = ClaimRepository(conn)
        self.titles = TitleService(conn)
        self.statements = StatementService(conn)
        self.payments = PaymentService(conn)
        self.ledger = Ledger(conn)
        self.audit = AuditLog(conn)

    def position(self, consignment_id: str, lien: Money | None = None) -> ProceedsPosition:
        """What is owed, what is held back and why."""
        consignment = self.consignments.get(consignment_id)
        holds: list[Hold] = []

        if consignment.status is not ConsignmentStatus.SOLD:
            holds.append(Hold("not_sold", "the unit has not sold"))

        run = self.sales.run_for_consignment(consignment_id)
        if run is None or run.status is not RunStatus.SOLD:
            holds.append(Hold("no_run", "there is no completed run for this unit"))
        else:
            holds.extend(self._buyer_holds(run.buyer_id))
            holds.extend(self._claim_holds(run.id or 0))

        holds.extend(self._title_holds(consignment_id))

        return ProceedsPosition(
            consignment_id=consignment_id,
            seller_id=consignment.seller_id,
            gross=self.statements.seller_statement(consignment.seller_id).balance,
            lien=lien or Money.zero(),
            holds=tuple(holds),
        )

    def _buyer_holds(self, buyer_id: str | None) -> list[Hold]:
        """Whether the buyer's account is clear.

        Deliberately the whole account rather than this sale. A buyer's money
        arrives as one wire against everything they bought that week, and
        earmarking it per sale would hold a seller's proceeds behind a unit the
        buyer has already covered.
        """
        if buyer_id is None:
            return [Hold("no_buyer", "the run has no buyer on it")]
        owed = self.statements.invoice(buyer_id).balance
        if owed.cents > 0:
            return [Hold("buyer_unpaid", f"the buyer still owes {owed}")]
        return []

    def _claim_holds(self, run_id: int) -> list[Hold]:
        open_claims = [
            claim
            for claim in self.claims.for_run(run_id)
            if claim.status in (ClaimStatus.OPEN, ClaimStatus.UNDER_REVIEW)
        ]
        if open_claims:
            return [
                Hold(
                    "arbitration_open",
                    f"{len(open_claims)} arbitration claims are still open",
                )
            ]
        return []

    def _title_holds(self, consignment_id: str) -> list[Hold]:
        record = self.titles.find(consignment_id)
        if record is None:
            return [Hold("no_title_record", "there is no title record for this unit")]
        if record.status not in (TitleStatus.ON_HAND, TitleStatus.SENT):
            return [Hold("title_absent", f"the title is {record.status.value}")]
        return []

    def release(
        self,
        consignment_id: str,
        *,
        lien: Money | None = None,
        lienholder: str | None = None,
        method: Method = Method.CHEQUE,
        reference: str | None = None,
    ) -> Money:
        """Pay the seller, after any lien comes off the top.

        Returns what the seller was actually handed, which is not the gross and
        is the number they ring up about.
        """
        position = self.position(consignment_id, lien)
        if not position.is_clear:
            raise PolicyViolation(
                "proceeds.held",
                "; ".join(hold.detail for hold in position.holds),
                consignment=consignment_id,
                holds=len(position.holds),
            )
        if position.lien.cents > 0:
            if lienholder is None:
                raise PolicyViolation(
                    "proceeds.no_lienholder",
                    "a payoff needs somebody to pay it to",
                    consignment=consignment_id,
                )
            self._post_lien(consignment_id, position, lienholder)
        net = position.net
        if net.cents <= 0:
            return Money.zero()
        self.payments.pay_out(
            position.seller_id,
            net,
            method=method,
            reference=reference,
            note=f"proceeds for {consignment_id}",
        )
        self.audit.record(
            "proceeds.released",
            "consignment",
            consignment_id,
            f"{net} to {position.seller_id}"
            + (f" after a {position.lien} payoff" if position.lien.cents else ""),
        )
        return net

    def _post_lien(
        self, consignment_id: str, position: ProceedsPosition, lienholder: str
    ) -> Entry:
        return self.ledger.post(
            Entry(
                reference=consignment_id,
                memo=f"lien payoff to {lienholder}",
                source="lien",
                source_id=consignment_id,
                lines=(
                    debit(Account.SELLER_PAYABLE, position.lien, position.seller_id),
                    credit(Account.LIEN_CLEARING, position.lien, lienholder),
                ),
            )
        )

    def ready_to_release(self) -> list[str]:
        """Every sold unit whose money can go, oldest first."""
        ready: list[str] = []
        for consignment in self.consignments.with_status(ConsignmentStatus.SOLD):
            if self.position(consignment.id).is_clear:
                ready.append(consignment.id)
        return ready

    def held(self) -> dict[str, tuple[Hold, ...]]:
        """Every sold unit that is stuck, and what is stopping it."""
        stuck: dict[str, tuple[Hold, ...]] = {}
        for consignment in self.consignments.with_status(ConsignmentStatus.SOLD):
            position = self.position(consignment.id)
            if not position.is_clear:
                stuck[consignment.id] = position.holds
        return stuck
