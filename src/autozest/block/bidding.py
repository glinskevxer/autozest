"""Bidding, the fall of the hammer, and the if bid that follows a short one.

Most units do not sell on the block. They reach a number the seller has not
agreed to, the auctioneer says "on if", and the unit goes onto a list that the
seller works through afterwards. Getting that right matters more than the
bidding itself, because a sale that is agreed at nine the next morning is still
a sale of a unit that ran under Tuesday's light.

Three rules here are not obvious and all three came from arguments.

A buyer cannot buy their own consignment. It sounds like it would never happen
and it happens constantly, because a dealer group consigns under one account
and bids under another belonging to the same group.

A bid below the standing high bid plus the increment is not a bid. Taking it
and sorting it out later means the block screen and the auctioneer disagree in
front of a room of people.

An if bid that the seller counters is a new offer to the buyer, not a binding
price. The buyer can walk, and if they do the unit is a no sale rather than a
sale at the buyer's own number.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autozest.block.models import Run, RunStatus
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import AccountRepository
from autozest.consignment.models import ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import PolicyViolation, ValidationError
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, one, update


class BidKind(Enum):
    LIVE = "live"
    PROXY = "proxy"
    FLOOR = "floor"
    COUNTER = "counter"
    ACCEPT = "accept"
    DECLINE = "decline"


#: Bid increments by price band. The band is decided by the standing bid.
INCREMENTS: tuple[tuple[Money, Money], ...] = (
    (Money.parse("1000.00"), Money.parse("25.00")),
    (Money.parse("5000.00"), Money.parse("50.00")),
    (Money.parse("15000.00"), Money.parse("100.00")),
    (Money.parse("50000.00"), Money.parse("250.00")),
)
TOP_INCREMENT = Money.parse("500.00")


def increment_at(standing: Money) -> Money:
    """The smallest step the auctioneer will take from here."""
    for ceiling, step in INCREMENTS:
        if standing < ceiling:
            return step
    return TOP_INCREMENT


@dataclass(frozen=True)
class Bid:
    """One bid on one run."""

    run_id: int
    amount: Money
    kind: BidKind
    buyer_id: str | None = None
    made_at: datetime | None = None


class BiddingService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sales = SaleRepository(conn)
        self.accounts = AccountRepository(conn)
        self.consignments = ConsignmentRepository(conn)

    def standing_bid(self, run_id: int) -> Bid | None:
        """The highest live bid so far."""
        row = one(
            self.conn,
            "SELECT run_id, buyer_id, amount_cents, kind FROM bid WHERE run_id = ? "
            "AND kind IN ('live', 'proxy', 'floor') "
            "ORDER BY amount_cents DESC, id DESC LIMIT 1",
            (run_id,),
        )
        if row is None:
            return None
        return Bid(
            run_id=row["run_id"],
            amount=Money(row["amount_cents"]),
            kind=BidKind(row["kind"]),
            buyer_id=row["buyer_id"],
        )

    def bids(self, run_id: int) -> list[Bid]:
        rows = all_rows(
            self.conn,
            "SELECT run_id, buyer_id, amount_cents, kind, made_at FROM bid "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        return [
            Bid(
                run_id=row["run_id"],
                amount=Money(row["amount_cents"]),
                kind=BidKind(row["kind"]),
                buyer_id=row["buyer_id"],
                made_at=datetime.fromisoformat(row["made_at"]),
            )
            for row in rows
        ]

    def bid(
        self, run_id: int, buyer_id: str, amount: Money, kind: BidKind = BidKind.LIVE
    ) -> Bid:
        """Take a bid, or say why it is not one."""
        run = self.sales.run(run_id)
        self._require_live(run)
        buyer = self.accounts.get(buyer_id)
        buyer.require_can_bid()
        self._require_not_own_unit(run, buyer_id)
        if amount.is_negative:
            raise ValidationError("a bid cannot be negative")

        standing = self.standing_bid(run_id)
        if standing is not None:
            step = increment_at(standing.amount)
            wanted = standing.amount + step
            if amount < wanted:
                raise PolicyViolation(
                    "bid.increment",
                    f"the next bid is {wanted}",
                    run=run.number,
                    standing=str(standing.amount),
                    offered=str(amount),
                )
        self._record(run_id, buyer_id, amount, kind)
        return Bid(run_id=run_id, amount=amount, kind=kind, buyer_id=buyer_id)

    def _record(self, run_id: int, buyer_id: str | None, amount: Money, kind: BidKind) -> None:
        insert(
            self.conn,
            "bid",
            {
                "run_id": run_id,
                "buyer_id": buyer_id,
                "amount_cents": amount.cents,
                "kind": kind.value,
                "made_at": now_text(),
            },
        )

    def _require_live(self, run: Run) -> None:
        self.sales.require_open(run.sale_code)
        if run.status not in (RunStatus.SCHEDULED, RunStatus.IF_BID):
            raise PolicyViolation(
                "run.finished",
                "that unit has already been through",
                run=run.number,
                status=run.status.value,
            )

    def _require_not_own_unit(self, run: Run, buyer_id: str) -> None:
        consignment = self.consignments.get(run.consignment_id)
        if consignment.seller_id == buyer_id:
            raise PolicyViolation(
                "bid.own_unit",
                "a seller cannot bid on their own consignment",
                run=run.number,
                account=buyer_id,
            )

    def fall(self, run_id: int) -> Run:
        """Drop the hammer on whatever the standing bid is.

        Above the floor it sells. Below it, or with no bid at all, it goes on
        if and the seller decides later. No floor at all means the unit is on
        the market and the high bid takes it.
        """
        run = self.sales.run(run_id)
        self._require_live(run)
        standing = self.standing_bid(run_id)
        if standing is None or standing.buyer_id is None:
            return self._settle(run, RunStatus.NO_SALE, None, None)

        consignment = self.consignments.get(run.consignment_id)
        floor = consignment.floor
        if floor is None or standing.amount >= floor:
            return self._settle(run, RunStatus.SOLD, standing.amount, standing.buyer_id)
        return self._settle(run, RunStatus.IF_BID, standing.amount, standing.buyer_id)

    def _settle(
        self, run: Run, status: RunStatus, hammer: Money | None, buyer_id: str | None
    ) -> Run:
        moved = run.settled_as(status, hammer, buyer_id)
        assert run.id is not None
        update(
            self.conn,
            "run",
            {"id": run.id},
            {
                "status": moved.status.value,
                "hammer_cents": moved.hammer.cents if moved.hammer is not None else None,
                "buyer_id": moved.buyer_id,
                "ran_at": now_text(),
            },
        )
        if status is not RunStatus.IF_BID:
            self._close_consignment(run, status)
        return self.sales.run(run.id)

    def _close_consignment(self, run: Run, status: RunStatus) -> None:
        consignment = self.consignments.get(run.consignment_id)
        if consignment.status is not ConsignmentStatus.CATALOGUED:
            return
        wanted = (
            ConsignmentStatus.SOLD if status is RunStatus.SOLD else ConsignmentStatus.NO_SALE
        )
        self.consignments.save_status(consignment, wanted)

    def counter(self, run_id: int, amount: Money) -> Bid:
        """The seller comes back with a number of their own."""
        run = self._require_if_bid(run_id)
        standing = self.standing_bid(run_id)
        if standing is not None and amount <= standing.amount:
            raise PolicyViolation(
                "if_bid.counter_below",
                "a counter has to be above the bid it is answering",
                run=run.number,
                bid=str(standing.amount),
                counter=str(amount),
            )
        self._record(run_id, None, amount, BidKind.COUNTER)
        return Bid(run_id=run_id, amount=amount, kind=BidKind.COUNTER)

    def latest_counter(self, run_id: int) -> Bid | None:
        row = one(
            self.conn,
            "SELECT run_id, amount_cents, kind FROM bid WHERE run_id = ? AND kind = 'counter' "
            "ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        if row is None:
            return None
        return Bid(
            run_id=row["run_id"], amount=Money(row["amount_cents"]), kind=BidKind.COUNTER
        )

    def accept(self, run_id: int) -> Run:
        """The seller takes the bid, or the buyer takes the counter."""
        run = self._require_if_bid(run_id)
        counter = self.latest_counter(run_id)
        standing = self.standing_bid(run_id)
        price = (
            counter.amount if counter is not None else (standing.amount if standing else None)
        )
        if price is None or standing is None or standing.buyer_id is None:
            raise PolicyViolation(
                "if_bid.nothing_to_accept", "there is no bid to accept", run=run.number
            )
        self._record(run_id, standing.buyer_id, price, BidKind.ACCEPT)
        return self._settle(run, RunStatus.SOLD, price, standing.buyer_id)

    def decline(self, run_id: int) -> Run:
        """Nobody moved. The unit did not sell."""
        run = self._require_if_bid(run_id)
        standing = self.standing_bid(run_id)
        self._record(
            run_id,
            standing.buyer_id if standing else None,
            standing.amount if standing else Money.zero(),
            BidKind.DECLINE,
        )
        return self._settle(run, RunStatus.NO_SALE, None, None)

    def _require_if_bid(self, run_id: int) -> Run:
        run = self.sales.run(run_id)
        if run.status is not RunStatus.IF_BID:
            raise PolicyViolation(
                "if_bid.not_pending",
                "that unit is not on if",
                run=run.number,
                status=run.status.value,
            )
        return run

    def pending_if_bids(self, sale_code: str) -> list[Run]:
        return [run for run in self.sales.runs(sale_code) if run.status is RunStatus.IF_BID]
