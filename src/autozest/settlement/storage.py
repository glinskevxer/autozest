"""Storage charges on units that will not leave.

The yard has room for about six hundred units and a sale puts three hundred
through it, so a unit sitting on the ground for six weeks is not free even
though nobody is doing anything to it.

Free days are not one number. They depend on what happened to the unit, and
the reasoning is worth writing down because it looks arbitrary and is not:

* a unit that **sold** starts its clock at the sale and gets a short window,
  because the buyer has been told it is theirs and the only thing stopping
  them collecting is them
* a unit that **did not sell** gets longer, because the seller has to arrange
  transport they were not expecting to need
* a unit **stuck behind a hold** is not charged at all while the hold stands.
  Charging a buyer storage on a unit we will not release the title for is how
  you lose a buyer.

That last rule is the one that keeps this module tied to the rest of the
system. Storage is a settlement question that cannot be answered without the
title desk and the arbitration desk.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from autozest.block.models import RunStatus
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import AccountClass, AccountRepository
from autozest.consignment.models import ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import ValidationError
from autozest.money import Money
from autozest.settlement.fees import FeeSchedule, Service
from autozest.settlement.proceeds import ProceedsService

#: Free days after a sale, by what happened to the unit.
FREE_DAYS_SOLD = 5
FREE_DAYS_NO_SALE = 10

#: Fleet and factory contracts carry a longer allowance.
CONTRACT_CLASSES = frozenset({AccountClass.FLEET_LEASE, AccountClass.FACTORY})
FREE_DAYS_CONTRACT = 21


@dataclass(frozen=True)
class StorageCharge:
    """What one unit owes for sitting there, and why it is that number."""

    consignment_id: str
    payer_id: str
    since: date
    free_days: int
    charged_days: int
    rate: Money
    held: bool

    @property
    def amount(self) -> Money:
        return self.rate.times(self.charged_days)

    @property
    def is_free(self) -> bool:
        return self.charged_days == 0

    def explain(self) -> str:
        if self.held:
            return "not charged, the unit is held behind something we own"
        if self.is_free:
            return f"inside the {self.free_days} free days"
        return f"{self.charged_days} days at {self.rate} after {self.free_days} free"


class StorageService:
    def __init__(self, conn: sqlite3.Connection, schedule: FeeSchedule | None = None) -> None:
        self.conn = conn
        self.schedule = schedule or FeeSchedule()
        self.consignments = ConsignmentRepository(conn)
        self.sales = SaleRepository(conn)
        self.accounts = AccountRepository(conn)
        self.proceeds = ProceedsService(conn)

    def free_days_for(self, consignment_id: str) -> int:
        """How long this unit sits for nothing."""
        consignment = self.consignments.get(consignment_id)
        seller = self.accounts.get(consignment.seller_id)
        if seller.account_class in CONTRACT_CLASSES:
            return FREE_DAYS_CONTRACT
        if consignment.status is ConsignmentStatus.SOLD:
            return FREE_DAYS_SOLD
        return FREE_DAYS_NO_SALE

    def charge_for(self, consignment_id: str, as_at: date | None = None) -> StorageCharge:
        """What this unit owes today."""
        today = as_at or date.today()
        consignment = self.consignments.get(consignment_id)
        run = self.sales.run_for_consignment(consignment_id)
        since = self._clock_starts(consignment_id, run_sale_date(self.sales, run))
        free = self.free_days_for(consignment_id)
        held = self._is_held(consignment_id, consignment.status)
        elapsed = (today - since).days
        charged = 0 if held else max(0, elapsed - free)
        payer = (
            run.buyer_id
            if run is not None and run.status is RunStatus.SOLD and run.buyer_id
            else consignment.seller_id
        )
        return StorageCharge(
            consignment_id=consignment_id,
            payer_id=payer,
            since=since,
            free_days=free,
            charged_days=charged,
            rate=self.schedule.service(Service.STORAGE_PER_DAY),
            held=held,
        )

    def _clock_starts(self, consignment_id: str, sale_day: date | None) -> date:
        """The sale day, or the arrival if the unit never ran."""
        if sale_day is not None:
            return sale_day
        consignment = self.consignments.get(consignment_id)
        if consignment.arrived_at is not None:
            return consignment.arrived_at.date()
        raise ValidationError(
            f"{consignment_id} has never arrived, there is nothing to charge storage on"
        )

    def _is_held(self, consignment_id: str, status: ConsignmentStatus) -> bool:
        """Whether something we own is what is stopping the unit leaving."""
        if status is not ConsignmentStatus.SOLD:
            return False
        position = self.proceeds.position(consignment_id)
        ours = {"no_title_record", "title_absent", "arbitration_open"}
        return any(hold.code in ours for hold in position.holds)

    def outstanding(self, as_at: date | None = None) -> list[StorageCharge]:
        """Every unit on the ground that owes something, biggest first."""
        charges: list[StorageCharge] = []
        for consignment in self.consignments.with_status(ConsignmentStatus.SOLD):
            charges.append(self.charge_for(consignment.id, as_at))
        for consignment in self.consignments.with_status(ConsignmentStatus.NO_SALE):
            charges.append(self.charge_for(consignment.id, as_at))
        return sorted(
            [charge for charge in charges if not charge.is_free],
            key=lambda charge: -charge.amount.cents,
        )


def run_sale_date(sales: SaleRepository, run: object) -> date | None:
    """The day a run's sale was held, or ``None`` when the unit never ran."""
    if run is None:
        return None
    code = getattr(run, "sale_code", None)
    if code is None:
        return None
    return sales.get(code).sale_date
