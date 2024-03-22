"""Building the run order.

Cataloguing is where the inspection floor's work turns into a light. Nobody
picks the light by hand: it comes from the announcements, the grade, the floor
price and the seller's class, and it is written onto the run so it stays put.

The run order itself is not arbitrary either. Two rules shape it and both exist
because of how a room of buyers behaves.

Sellers are spread out. Twenty units from one fleet running back to back is
twenty units competing with each other, and the last eight bring less than the
first eight. So consecutive runs from the same seller are pushed apart as far
as the lane allows.

Red units are not front loaded. A lane that opens with six as-is units empties
the seats, and the buyers who leave are not there for run twenty.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from autozest.block.light import LightDecision, assign
from autozest.block.models import Run
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import AccountRepository
from autozest.consignment.models import Consignment, ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import PolicyViolation
from autozest.inspection.announcements import AnnouncementCode, Light
from autozest.inspection.service import InspectionService
from autozest.storage.records import update

#: How many runs apart two units from the same seller should be, if possible.
SELLER_SPACING = 4

#: The first stretch of a lane that red units are kept out of.
FRONT_OF_LANE = 6


@dataclass(frozen=True)
class Placement:
    """One unit, its light and where it landed."""

    consignment: Consignment
    decision: LightDecision
    lane: str
    position: int

    @property
    def light(self) -> Light:
        return self.decision.light


class CatalogueService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sales = SaleRepository(conn)
        self.consignments = ConsignmentRepository(conn)
        self.inspection = InspectionService(conn)
        self.accounts = AccountRepository(conn)

    def decide_light(
        self, consignment: Consignment, *, guaranteed: bool = False
    ) -> LightDecision:
        """Work out the light for one unit from everything known about it."""
        report = self.inspection.latest_for(consignment.id)
        announcements: set[AnnouncementCode] = self.inspection.announcements(consignment.id)
        seller = self.accounts.get(consignment.seller_id)
        grade: Decimal | None = report.grade if report else None
        return assign(
            announcements=announcements,
            grade=grade,
            floor=consignment.floor,
            seller_class=seller.account_class,
            guaranteed=guaranteed,
        )

    def place(
        self,
        sale_code: str,
        consignment_id: str,
        lane: str,
        *,
        guaranteed: bool = False,
        position: int | None = None,
    ) -> Placement:
        """Put one unit in a lane, deciding its light on the way in."""
        consignment = self.consignments.get(consignment_id)
        if consignment.status is not ConsignmentStatus.INSPECTED:
            raise PolicyViolation(
                "catalogue.not_inspected",
                "a unit is catalogued once it has a signed report",
                consignment=consignment_id,
                status=consignment.status.value,
            )
        decision = self.decide_light(consignment, guaranteed=guaranteed)
        run = self.sales.add_run(
            sale_code, lane, consignment_id, decision.light, position=position
        )
        self.consignments.save_status(consignment, ConsignmentStatus.CATALOGUED)
        return Placement(
            consignment=consignment,
            decision=decision,
            lane=run.lane,
            position=run.position,
        )

    def ready(self) -> list[Consignment]:
        """Everything inspected and not yet in a sale."""
        return self.consignments.with_status(ConsignmentStatus.INSPECTED)

    def fill(
        self, sale_code: str, lane: str, consignment_ids: list[str] | None = None
    ) -> list[Placement]:
        """Catalogue a whole lane, in an order the room will sit through."""
        if consignment_ids is None:
            units = self.ready()
        else:
            units = [self.consignments.get(cid) for cid in consignment_ids]
        decisions = {unit.id: self.decide_light(unit) for unit in units}
        ordered = order_runs(units, {uid: d.light for uid, d in decisions.items()})
        placed: list[Placement] = []
        for unit in ordered:
            placed.append(self.place(sale_code, unit.id, lane))
        return placed

    def lane_summary(self, sale_code: str, lane: str) -> dict[Light, int]:
        """How many of each light are in a lane, for the block office board."""
        counts: dict[Light, int] = {light: 0 for light in Light}
        for run in self.sales.runs(sale_code, lane.upper()):
            counts[run.light] += 1
        return counts

    def relight(self, run_id: int, light: Light, reason: str) -> Run:
        """Change a light by hand, before the sale opens and never after."""
        run = self.sales.run(run_id)
        sale = self.sales.get(run.sale_code)
        if not sale.status.accepts_catalogue_changes:
            raise PolicyViolation(
                "catalogue.frozen",
                "the light cannot move once the sale is open",
                run=run.number,
                sale=sale.code,
            )
        if not reason.strip():
            raise PolicyViolation(
                "catalogue.relight_reason",
                "moving a light by hand needs a reason on the record",
                run=run.number,
            )
        update(self.conn, "run", {"id": run_id}, {"light": light.value})
        return self.sales.run(run_id)


def order_runs(units: list[Consignment], lights: dict[str, Light]) -> list[Consignment]:
    """Order units for a lane.

    Same seller spread apart, red units kept out of the front. The order is
    stable for a given input so a run sheet reprinted is the same run sheet.
    """
    remaining = sorted(units, key=lambda unit: unit.id)
    ordered: list[Consignment] = []
    while remaining:
        pick = _next_unit(remaining, ordered, lights)
        remaining.remove(pick)
        ordered.append(pick)
    return ordered


def _next_unit(
    remaining: list[Consignment], ordered: list[Consignment], lights: dict[str, Light]
) -> Consignment:
    recent = {unit.seller_id for unit in ordered[-SELLER_SPACING:]}
    front = len(ordered) < FRONT_OF_LANE
    for unit in remaining:
        if unit.seller_id in recent:
            continue
        if front and lights.get(unit.id) is Light.RED:
            continue
        return unit
    for unit in remaining:
        if unit.seller_id not in recent:
            return unit
    return remaining[0]
