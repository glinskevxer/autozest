"""Absentee bids.

A buyer who cannot be in the room leaves a maximum with the block. The block
bids for them one increment at a time and stops at their number, and the number
is never shown to anybody, which is the entire point of leaving one.

Two rules here are the ones that get argued about.

**A proxy pays one increment over the next best bid, not its own maximum.** A
buyer who leaves twelve thousand against a room that stops at nine thousand two
hundred pays nine thousand three hundred. Charging the maximum is the obvious
implementation and it is theft.

**Two proxies against each other settle between themselves.** The higher one
wins at one increment over the lower, and if they are identical the earlier one
wins at that figure, because the later buyer had the chance to beat a number
they could not see and chose not to.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autozest.block.bidding import BiddingService, BidKind, increment_at
from autozest.block.models import Run, RunStatus
from autozest.catalog.accounts import AccountRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import Conflict, PolicyViolation, ValidationError
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, update


@dataclass(frozen=True)
class ProxyBid:
    """One absentee maximum."""

    id: int
    run_id: int
    buyer_id: str
    maximum: Money
    placed_at: datetime
    withdrawn_at: datetime | None = None

    @property
    def is_live(self) -> bool:
        return self.withdrawn_at is None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ProxyBid:
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            buyer_id=row["buyer_id"],
            maximum=Money(row["maximum_cents"]),
            placed_at=datetime.fromisoformat(row["placed_at"]),
            withdrawn_at=(
                datetime.fromisoformat(row["withdrawn_at"]) if row["withdrawn_at"] else None
            ),
        )


@dataclass(frozen=True)
class ProxyOutcome:
    """What the proxies settle at, before anything is bid."""

    winner: ProxyBid | None
    price: Money | None
    beaten: tuple[ProxyBid, ...] = ()

    @property
    def has_winner(self) -> bool:
        return self.winner is not None


class ProxyService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.bidding = BiddingService(conn)
        self.accounts = AccountRepository(conn)
        self.consignments = ConsignmentRepository(conn)

    def leave(self, run_id: int, buyer_id: str, maximum: Money) -> ProxyBid:
        """Leave a maximum against a run."""
        run = self.bidding.sales.run(run_id)
        if run.status is not RunStatus.SCHEDULED:
            raise PolicyViolation(
                "proxy.too_late",
                "that unit has already run",
                run=run.number,
                status=run.status.value,
            )
        if maximum.cents <= 0:
            raise ValidationError("a proxy bid has to be for something")
        self.accounts.get(buyer_id).require_can_bid()
        if self.consignments.get(run.consignment_id).seller_id == buyer_id:
            raise PolicyViolation(
                "bid.own_unit",
                "a seller cannot leave a proxy on their own consignment",
                run=run.number,
                account=buyer_id,
            )
        if any(p.buyer_id == buyer_id and p.is_live for p in self.on(run_id)):
            raise Conflict("that buyer already has a proxy on this run", run=run.number)
        insert(
            self.conn,
            "proxy_bid",
            {
                "run_id": run_id,
                "buyer_id": buyer_id,
                "maximum_cents": maximum.cents,
                "placed_at": now_text(),
            },
        )
        return self.on(run_id)[-1]

    def withdraw(self, run_id: int, buyer_id: str) -> None:
        update(
            self.conn,
            "proxy_bid",
            {"run_id": run_id, "buyer_id": buyer_id},
            {"withdrawn_at": now_text()},
        )

    def on(self, run_id: int) -> list[ProxyBid]:
        rows = all_rows(
            self.conn, "SELECT * FROM proxy_bid WHERE run_id = ? ORDER BY id", (run_id,)
        )
        return [ProxyBid.from_row(row) for row in rows]

    def live(self, run_id: int) -> list[ProxyBid]:
        return [proxy for proxy in self.on(run_id) if proxy.is_live]

    def settle(self, run_id: int, standing: Money | None = None) -> ProxyOutcome:
        """Work out where the proxies land, without bidding anything.

        The winner pays one increment over whichever is higher, the room's
        standing bid or the second best maximum. Never their own number.
        """
        proxies = sorted(self.live(run_id), key=lambda p: (-p.maximum.cents, p.id))
        if not proxies:
            return ProxyOutcome(winner=None, price=None)
        winner = proxies[0]
        runner_up = proxies[1].maximum if len(proxies) > 1 else None
        floor_price = max(
            [amount for amount in (standing, runner_up) if amount is not None],
            key=lambda amount: amount.cents,
            default=None,
        )
        if floor_price is None:
            price = winner.maximum
        else:
            price = min(floor_price + increment_at(floor_price), winner.maximum)
        if standing is not None and price <= standing:
            return ProxyOutcome(winner=None, price=None, beaten=tuple(proxies))
        return ProxyOutcome(winner=winner, price=price, beaten=tuple(proxies[1:]))

    def execute(self, run_id: int) -> Run:
        """Bid the proxies in, then drop the hammer."""
        standing = self.bidding.standing_bid(run_id)
        outcome = self.settle(run_id, standing.amount if standing else None)
        if outcome.winner is not None and outcome.price is not None:
            self.bidding.bid(run_id, outcome.winner.buyer_id, outcome.price, kind=BidKind.PROXY)
        return self.bidding.fall(run_id)
