"""Running a unit again.

About a fifth of what crosses the block does not sell, and most of that runs
again the following week. Two things about that are not obvious.

**The light is worked out again.** A unit that ran green in March and picked up
a frame finding on a second inspection runs yellow in April. Carrying the old
light forward is the kind of shortcut that ends in a buy back, because the
buyer in April heard nothing about the rail.

**The seller pays for the miss.** A no sale fee and a rerun fee are separate
charges for separate things: the first is the cost of the run that did not
work, the second is the cost of the next one. Sellers who ask for a floor
nobody will pay learn faster when the two are itemised.

There is a limit. After three misses the unit is not making it to a bid and
running it a fourth time wastes a slot somebody else wants.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from autozest.block.catalogue import CatalogueService
from autozest.block.models import Run, RunStatus
from autozest.block.sales import SaleRepository
from autozest.consignment.models import ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import PolicyViolation
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.settlement.fees import FeeSchedule, Service
from autozest.settlement.postings import PostingService

#: How many times a unit may run before somebody has to look at the floor.
MAX_RUNS = 3


@dataclass(frozen=True)
class RerunPlan:
    """What running this unit again would mean."""

    consignment_id: str
    previous_runs: int
    previous_light: Light
    new_light: Light
    no_sale_fee: Money
    rerun_fee: Money
    reasons: tuple[str, ...]

    @property
    def light_changed(self) -> bool:
        return self.previous_light is not self.new_light

    @property
    def total_fees(self) -> Money:
        return self.no_sale_fee + self.rerun_fee

    def explain(self) -> str:
        moved = (
            f"{self.previous_light.value} to {self.new_light.value}"
            if self.light_changed
            else f"still {self.new_light.value}"
        )
        return f"run {self.previous_runs + 1}, {moved}, {self.total_fees} in fees"


class RerunService:
    def __init__(self, conn: sqlite3.Connection, schedule: FeeSchedule | None = None) -> None:
        self.conn = conn
        self.schedule = schedule or FeeSchedule()
        self.sales = SaleRepository(conn)
        self.consignments = ConsignmentRepository(conn)
        self.catalogue = CatalogueService(conn)
        self.postings = PostingService(conn, self.schedule)

    def runs_so_far(self, consignment_id: str) -> list[Run]:
        return [run for run in self._all_runs() if run.consignment_id == consignment_id]

    def _all_runs(self) -> list[Run]:
        """Every run there has ever been, oldest sale first.

        The order matters: callers take the last one as the most recent, and
        the sale listing comes back newest first for the screen it was written
        for. Reversing it here rather than in three callers.
        """
        runs: list[Run] = []
        for sale in sorted(self.sales.recent(limit=1000), key=lambda item: item.sale_date):
            runs.extend(self.sales.runs(sale.code))
        return runs

    def plan(self, consignment_id: str, *, guaranteed: bool = False) -> RerunPlan:
        """Work out the light and the fees without moving anything."""
        consignment = self.consignments.get(consignment_id)
        history = self.runs_so_far(consignment_id)
        if not history:
            raise PolicyViolation(
                "rerun.never_ran",
                "that unit has not been through a sale yet",
                consignment=consignment_id,
            )
        previous = history[-1]
        decision = self.catalogue.decide_light(consignment, guaranteed=guaranteed)
        return RerunPlan(
            consignment_id=consignment_id,
            previous_runs=len(history),
            previous_light=previous.light,
            new_light=decision.light,
            no_sale_fee=self.schedule.service(Service.NO_SALE),
            rerun_fee=self.schedule.service(Service.RERUN),
            reasons=decision.reasons,
        )

    def rerun(
        self,
        consignment_id: str,
        sale_code: str,
        lane: str,
        *,
        guaranteed: bool = False,
        charge: bool = True,
    ) -> RerunPlan:
        """Put a unit that did not sell into another sale.

        The light is decided from what is known now rather than carried
        forward, which is the whole point of doing this here rather than
        copying the old run.
        """
        consignment = self.consignments.get(consignment_id)
        if consignment.status is not ConsignmentStatus.NO_SALE:
            raise PolicyViolation(
                "rerun.not_a_no_sale",
                "only a unit that did not sell is run again",
                consignment=consignment_id,
                status=consignment.status.value,
            )
        planned = self.plan(consignment_id, guaranteed=guaranteed)
        if planned.previous_runs >= MAX_RUNS:
            raise PolicyViolation(
                "rerun.too_many",
                f"that unit has run {planned.previous_runs} times, the floor needs looking at",
                consignment=consignment_id,
                runs=planned.previous_runs,
            )
        if charge:
            self._charge(consignment_id)
        self.consignments.save_status(consignment, ConsignmentStatus.CATALOGUED)
        self.sales.add_run(sale_code, lane, consignment_id, planned.new_light)
        return planned

    def _charge(self, consignment_id: str) -> None:
        """Bill the seller for the miss and for the next attempt."""
        previous = self.runs_so_far(consignment_id)[-1]
        assert previous.id is not None
        self.postings.post_service(previous.id, Service.NO_SALE)
        self.postings.post_service(previous.id, Service.RERUN)

    def eligible(self) -> list[str]:
        """Every unit that did not sell and could run again."""
        found: list[str] = []
        for consignment in self.consignments.with_status(ConsignmentStatus.NO_SALE):
            if len(self.runs_so_far(consignment.id)) < MAX_RUNS:
                found.append(consignment.id)
        return found

    def exhausted(self) -> list[str]:
        """Units that have run out of attempts and need a conversation."""
        return [
            consignment.id
            for consignment in self.consignments.with_status(ConsignmentStatus.NO_SALE)
            if len(self.runs_so_far(consignment.id)) >= MAX_RUNS
        ]

    def sold_after_rerun(self) -> list[str]:
        """Units that failed once and then sold, for the sales manager."""
        found: list[str] = []
        for consignment in self.consignments.with_status(ConsignmentStatus.SOLD):
            history = self.runs_so_far(consignment.id)
            if len(history) > 1 and history[-1].status is RunStatus.SOLD:
                found.append(consignment.id)
        return found
