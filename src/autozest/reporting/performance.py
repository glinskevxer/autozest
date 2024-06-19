"""How a seller and a buyer are actually doing.

Two reports, and the useful one is not the obvious one. Gross is easy to
produce and tells nobody anything: a fleet that consigns four hundred units a
year will always be top of a gross list. What the sales manager rings people
about is the ratio between what a seller asks and what the room pays, and how
often their units come back.

So the seller report leads with sell through and the gap to the floor, and the
arbitration rate sits beside them, because a seller with a high sell through
and a high arbitration rate is not a good seller. They are one whose condition
reports are optimistic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from autozest.arbitration.claims import ClaimRepository, ClaimStatus
from autozest.block.models import Run, RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.money import Money, total


def _rate(part: int, whole: int) -> Decimal:
    if whole == 0:
        return Decimal("0.0")
    return (Decimal(part) * 100 / Decimal(whole)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class SellerRecord:
    """One seller across one sale, or across everything."""

    seller_id: str
    consigned: int
    ran: int
    sold: int
    gross: Money
    floor_gap: Money
    claims: int
    upheld: int

    @property
    def sell_through(self) -> Decimal:
        return _rate(self.sold, self.ran)

    @property
    def arbitration_rate(self) -> Decimal:
        return _rate(self.claims, self.sold)

    @property
    def upheld_rate(self) -> Decimal:
        return _rate(self.upheld, self.claims)

    @property
    def average(self) -> Money:
        return Money(self.gross.cents // self.sold) if self.sold else Money.zero()

    @property
    def is_optimistic(self) -> bool:
        """A high sell through beside a high arbitration rate is a warning.

        It usually means the condition reports are generous rather than that
        the units are good.
        """
        return self.sell_through >= Decimal("70.0") and self.arbitration_rate >= Decimal("15.0")


@dataclass(frozen=True)
class BuyerRecord:
    """One buyer across one sale, or across everything."""

    buyer_id: str
    bought: int
    spend: Money
    claims: int
    upheld: int

    @property
    def average(self) -> Money:
        return Money(self.spend.cents // self.bought) if self.bought else Money.zero()

    @property
    def claim_rate(self) -> Decimal:
        return _rate(self.claims, self.bought)


class PerformanceService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sales = SaleRepository(conn)
        self.consignments = ConsignmentRepository(conn)
        self.claims = ClaimRepository(conn)

    def _runs(self, sale_code: str | None) -> list[Run]:
        if sale_code is not None:
            return self.sales.runs(sale_code)
        runs: list[Run] = []
        for sale in self.sales.recent(limit=1000):
            runs.extend(self.sales.runs(sale.code))
        return runs

    def sellers(self, sale_code: str | None = None) -> list[SellerRecord]:
        """Every seller who put something through, worst sell through last."""
        buckets: dict[str, list[Run]] = {}
        for run in self._runs(sale_code):
            seller = self.consignments.get(run.consignment_id).seller_id
            buckets.setdefault(seller, []).append(run)
        records = [self._seller_record(seller, runs) for seller, runs in buckets.items()]
        return sorted(records, key=lambda record: (-record.sell_through, record.seller_id))

    def _seller_record(self, seller_id: str, runs: list[Run]) -> SellerRecord:
        sold = [run for run in runs if run.status is RunStatus.SOLD]
        ran = [run for run in runs if run.status is not RunStatus.PULLED]
        claims, upheld = self._claim_counts(sold)
        return SellerRecord(
            seller_id=seller_id,
            consigned=len(runs),
            ran=len(ran),
            sold=len(sold),
            gross=total([run.hammer for run in sold if run.hammer]),
            floor_gap=self._floor_gap(sold),
            claims=claims,
            upheld=upheld,
        )

    def _floor_gap(self, sold: list[Run]) -> Money:
        """How far above or below the asking price the room actually paid."""
        gap = Money.zero()
        for run in sold:
            consignment = self.consignments.get(run.consignment_id)
            if consignment.floor is not None and run.hammer is not None:
                gap = gap + (run.hammer - consignment.floor)
        return gap

    def _claim_counts(self, runs: list[Run]) -> tuple[int, int]:
        claims = 0
        upheld = 0
        for run in runs:
            for claim in self.claims.for_run(run.id or 0):
                claims += 1
                if claim.status is ClaimStatus.RESOLVED:
                    upheld += 1
        return claims, upheld

    def buyers(self, sale_code: str | None = None) -> list[BuyerRecord]:
        """Every buyer who bought something, biggest spend first."""
        buckets: dict[str, list[Run]] = {}
        for run in self._runs(sale_code):
            if run.status is RunStatus.SOLD and run.buyer_id:
                buckets.setdefault(run.buyer_id, []).append(run)
        records = []
        for buyer, runs in buckets.items():
            claims, upheld = self._claim_counts(runs)
            records.append(
                BuyerRecord(
                    buyer_id=buyer,
                    bought=len(runs),
                    spend=total([run.hammer for run in runs if run.hammer]),
                    claims=claims,
                    upheld=upheld,
                )
            )
        return sorted(records, key=lambda record: -record.spend.cents)

    def seller(self, seller_id: str, sale_code: str | None = None) -> SellerRecord:
        for record in self.sellers(sale_code):
            if record.seller_id == seller_id:
                return record
        return SellerRecord(seller_id, 0, 0, 0, Money.zero(), Money.zero(), 0, 0)
