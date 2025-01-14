"""Is this sale ready to open.

Run on the Monday. It is the list somebody walks with a clipboard, and every
item on it is something that has stopped a lane at some point.

The checks are ordered by how long they take to fix. A missing auctioneer is a
phone call. A unit with no signed condition report is an inspector and an hour.
A title that has not arrived is six weeks and the unit runs blue or not at all.
Putting them in that order is the difference between a list somebody works
through and a list somebody reads.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from autozest.block.models import Run, SaleStatus
from autozest.block.registration import RegistrationService
from autozest.block.roster import RosterService
from autozest.block.sales import SaleRepository
from autozest.consignment.repository import ConsignmentRepository
from autozest.inspection.announcements import Light
from autozest.inspection.service import InspectionService
from autozest.title.service import TitleService, TitleStatus


@dataclass(frozen=True)
class Item:
    """One thing to do before the sale opens."""

    order: int
    name: str
    detail: str
    blocking: bool = True

    def __str__(self) -> str:
        mark = "MUST" if self.blocking else "note"
        return f"{mark}  {self.name}: {self.detail}"


@dataclass(frozen=True)
class Readiness:
    """The clipboard."""

    sale_code: str
    catalogued: int
    items: tuple[Item, ...]

    @property
    def blockers(self) -> tuple[Item, ...]:
        return tuple(item for item in self.items if item.blocking)

    @property
    def can_open(self) -> bool:
        return not self.blockers

    def summary(self) -> str:
        if self.can_open:
            notes = len(self.items)
            tail = f", {notes} things to note" if notes else ""
            return f"{self.catalogued} units, ready{tail}"
        return f"{self.catalogued} units, {len(self.blockers)} things must be done first"


def check(conn: sqlite3.Connection, sale_code: str) -> Readiness:
    """Walk the sale and say what is stopping it."""
    sales = SaleRepository(conn)
    sale = sales.get(sale_code)
    runs = sales.runs(sale_code)
    items: list[Item] = []

    if sale.status is not SaleStatus.PLANNED:
        items.append(Item(0, "sale", f"already {sale.status.value}", blocking=False))

    if not runs:
        items.append(Item(1, "catalogue", "nothing is catalogued"))

    items.extend(_roster_items(conn, sale_code))
    items.extend(_door_items(conn, sale_code))
    items.extend(_report_items(conn, runs))
    items.extend(_title_items(conn, runs))

    return Readiness(
        sale_code=sale_code,
        catalogued=len(runs),
        items=tuple(sorted(items, key=lambda item: (item.order, item.name))),
    )


def _roster_items(conn: sqlite3.Connection, sale_code: str) -> list[Item]:
    gaps = RosterService(conn).gaps(sale_code)
    return [
        Item(2, f"lane {lane}", f"needs {', '.join(missing)}")
        for lane, missing in sorted(gaps.items())
    ]


def _door_items(conn: sqlite3.Connection, sale_code: str) -> list[Item]:
    registrations = RegistrationService(conn).for_sale(sale_code)
    blocked = [entry for entry in registrations if not entry.status.may_bid]
    if not blocked:
        return []
    return [
        Item(
            3,
            "door list",
            f"{len(blocked)} badges are blocked",
            blocking=False,
        )
    ]


def _report_items(conn: sqlite3.Connection, runs: list[Run]) -> list[Item]:
    inspection = InspectionService(conn)
    missing = 0
    for run in runs:
        report = inspection.latest_for(run.consignment_id)
        if report is None or report.is_open:
            missing += 1
    if not missing:
        return []
    return [Item(4, "condition reports", f"{missing} units have no signed report")]


def _title_items(conn: sqlite3.Connection, runs: list[Run]) -> list[Item]:
    titles = TitleService(conn)
    consignments = ConsignmentRepository(conn)
    waiting = 0
    unmarked = 0
    for run in runs:
        record = titles.find(run.consignment_id)
        if record is None:
            unmarked += 1
            continue
        if record.status is not TitleStatus.ON_HAND:
            waiting += 1
            if run.light is not Light.BLUE:
                consignment = consignments.get(run.consignment_id)
                if consignment.title_expected:
                    unmarked += 1
    items: list[Item] = []
    if waiting:
        items.append(Item(5, "titles", f"{waiting} titles are not here yet", blocking=False))
    if unmarked:
        items.append(
            Item(
                5,
                "title lane",
                f"{unmarked} units have no title and are not running blue",
                blocking=False,
            )
        )
    return items


def render(conn: sqlite3.Connection, sale_code: str) -> list[str]:
    report = check(conn, sale_code)
    lines = [f"readiness {report.sale_code}"]
    lines.extend(f"  {item}" for item in report.items)
    lines.append(report.summary())
    return lines
