"""Sales and lanes, and the run order that hangs off them.

The catalogue is built while the sale is planned and frozen when it opens.
After that a unit can be pulled but nothing can be inserted, because the run
order is printed, the lot cards are on the windscreens and half the buyers are
working off a list they downloaded that morning.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from autozest.block.models import Lane, Run, RunStatus, Sale, SaleStatus
from autozest.errors import Conflict, NotFound, PolicyViolation
from autozest.identifiers import sale_code
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.storage.records import (
    all_rows,
    insert,
    now_text,
    one,
    one_or_raise,
    scalar,
    update,
)


@dataclass(frozen=True)
class RunSheetLine:
    """One line of a printed run sheet, with everything already joined on."""

    run_id: int
    number: str
    consignment_id: str
    vin: str
    description: str
    seller_id: str
    light: Light
    status: RunStatus
    buyer_id: str | None = None
    floor: Money | None = None
    hammer: Money | None = None


class SaleRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, when: date, title: str, lanes: dict[str, int]) -> Sale:
        """Plan a sale with its lanes. A sale without lanes is not a sale."""
        if not lanes:
            raise PolicyViolation(
                "sale.no_lanes", "a sale needs at least one lane", date=str(when)
            )
        code = sale_code(when, len(lanes))
        sale = Sale(code=code, sale_date=when, title=title)
        insert(
            self.conn,
            "sale",
            {
                "code": sale.code,
                "sale_date": when.isoformat(),
                "title": title,
                "status": sale.status.value,
                "created_at": now_text(),
            },
        )
        for letter, capacity in sorted(lanes.items()):
            self.add_lane(Lane(sale_code=code, letter=letter.upper(), capacity=capacity))
        return sale

    def add_lane(self, lane: Lane) -> Lane:
        sale = self.get(lane.sale_code)
        if not sale.status.accepts_catalogue_changes:
            raise PolicyViolation(
                "sale.frozen",
                "the catalogue is closed, a lane cannot be added now",
                sale=sale.code,
                status=sale.status.value,
            )
        insert(
            self.conn,
            "lane",
            {
                "sale_code": lane.sale_code,
                "letter": lane.letter,
                "capacity": lane.capacity,
                "auctioneer": lane.auctioneer,
            },
        )
        return lane

    def get(self, code: str) -> Sale:
        row = one_or_raise(
            self.conn, "SELECT * FROM sale WHERE code = ?", (code,), "sale", code
        )
        return Sale.from_row(row)

    def on(self, when: date) -> Sale | None:
        row = one(self.conn, "SELECT * FROM sale WHERE sale_date = ?", (when.isoformat(),))
        return Sale.from_row(row) if row else None

    def lanes(self, code: str) -> list[Lane]:
        rows = all_rows(
            self.conn, "SELECT * FROM lane WHERE sale_code = ? ORDER BY letter", (code,)
        )
        return [Lane.from_row(row) for row in rows]

    def lane(self, code: str, letter: str) -> Lane:
        row = one_or_raise(
            self.conn,
            "SELECT * FROM lane WHERE sale_code = ? AND letter = ?",
            (code, letter.upper()),
            "lane",
            f"{code}/{letter.upper()}",
        )
        return Lane.from_row(row)

    def recent(self, limit: int = 20) -> list[Sale]:
        rows = all_rows(
            self.conn, "SELECT * FROM sale ORDER BY sale_date DESC LIMIT ?", (limit,)
        )
        return [Sale.from_row(row) for row in rows]

    def open_sale(self, code: str, *, at: datetime | None = None) -> Sale:
        """Freeze the catalogue and start the sale."""
        sale = self.get(code).with_status(SaleStatus.OPEN)
        if not self.runs(code):
            raise PolicyViolation(
                "sale.empty", "there is nothing catalogued to sell", sale=code
            )
        update(
            self.conn,
            "sale",
            {"code": code},
            {
                "status": sale.status.value,
                "opened_at": (at or datetime.now().astimezone()).isoformat(timespec="seconds"),
            },
        )
        return self.get(code)

    def close_sale(self, code: str, *, at: datetime | None = None) -> Sale:
        """Close the sale. Anything still scheduled is treated as a no sale."""
        sale = self.get(code).with_status(SaleStatus.CLOSED)
        update(
            self.conn,
            "run",
            {"sale_code": code, "status": RunStatus.SCHEDULED.value},
            {"status": RunStatus.NO_SALE.value},
        )
        update(
            self.conn,
            "sale",
            {"code": code},
            {
                "status": sale.status.value,
                "closed_at": (at or datetime.now().astimezone()).isoformat(timespec="seconds"),
            },
        )
        return self.get(code)

    def settle(self, code: str) -> Sale:
        sale = self.get(code).with_status(SaleStatus.SETTLED)
        update(self.conn, "sale", {"code": code}, {"status": sale.status.value})
        return self.get(code)

    def runs(self, code: str, lane: str | None = None) -> list[Run]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM run WHERE sale_code = ? AND (? IS NULL OR lane = ?) "
            "ORDER BY lane, position",
            (code, lane, lane),
        )
        return [Run.from_row(row) for row in rows]

    def run(self, run_id: int) -> Run:
        row = one_or_raise(
            self.conn, "SELECT * FROM run WHERE id = ?", (run_id,), "run", run_id
        )
        return Run.from_row(row)

    def run_at(self, code: str, lane: str, position: int) -> Run:
        row = one_or_raise(
            self.conn,
            "SELECT * FROM run WHERE sale_code = ? AND lane = ? AND position = ?",
            (code, lane.upper(), position),
            "run",
            f"{lane.upper()}{position:03d}",
        )
        return Run.from_row(row)

    def run_for_consignment(self, consignment_id: str) -> Run | None:
        row = one(
            self.conn,
            "SELECT * FROM run WHERE consignment_id = ? ORDER BY id DESC LIMIT 1",
            (consignment_id,),
        )
        return Run.from_row(row) if row else None

    def next_position(self, code: str, lane: str) -> int:
        highest = scalar(
            self.conn,
            "SELECT MAX(position) FROM run WHERE sale_code = ? AND lane = ?",
            (code, lane.upper()),
        )
        return int(highest or 0) + 1

    def add_run(
        self,
        code: str,
        lane: str,
        consignment_id: str,
        light: Light,
        position: int | None = None,
    ) -> Run:
        """Put a unit in a lane. Only while the catalogue is open."""
        sale = self.get(code)
        if not sale.status.accepts_catalogue_changes:
            raise PolicyViolation(
                "sale.frozen",
                "the run order is printed, nothing more goes in",
                sale=code,
                status=sale.status.value,
            )
        target = self.lane(code, lane)
        used = len(self.runs(code, target.letter))
        if used >= target.capacity:
            raise PolicyViolation(
                "lane.full",
                f"lane {target.letter} holds {target.capacity} units",
                sale=code,
                lane=target.letter,
            )
        if self._already_catalogued(code, consignment_id):
            raise Conflict(
                "that unit is already in this sale", sale=code, consignment=consignment_id
            )
        where = position or self.next_position(code, target.letter)
        insert(
            self.conn,
            "run",
            {
                "sale_code": code,
                "lane": target.letter,
                "position": where,
                "consignment_id": consignment_id,
                "light": light.value,
                "status": RunStatus.SCHEDULED.value,
            },
        )
        return self.run_at(code, target.letter, where)

    def _already_catalogued(self, code: str, consignment_id: str) -> bool:
        found = scalar(
            self.conn,
            "SELECT 1 FROM run WHERE sale_code = ? AND consignment_id = ? LIMIT 1",
            (code, consignment_id),
        )
        return found is not None

    def pull(self, run_id: int) -> Run:
        """Take a unit out. Allowed right up until it crosses the block."""
        run = self.run(run_id)
        pulled = run.with_status(RunStatus.PULLED)
        update(self.conn, "run", {"id": run_id}, {"status": pulled.status.value})
        return self.run(run_id)

    def run_sheet(self, code: str, lane: str | None = None) -> list[RunSheetLine]:
        """The run order with the unit details already attached.

        The obvious way to print a run sheet is to walk the runs and look up
        each consignment and vehicle, which is three hundred round trips for a
        sale of three hundred. This is one.
        """
        rows = all_rows(
            self.conn,
            "SELECT r.id, r.lane, r.position, r.light, r.status, r.hammer_cents, "
            "r.buyer_id, r.consignment_id, c.seller_id, c.floor_cents, c.vin, "
            "v.model_year, v.make, v.model, v.series "
            "FROM run r "
            "JOIN consignment c ON c.id = r.consignment_id "
            "JOIN vehicle v ON v.vin = c.vin "
            "WHERE r.sale_code = ? AND (? IS NULL OR r.lane = ?) "
            "ORDER BY r.lane, r.position",
            (code, lane, lane),
        )
        return [
            RunSheetLine(
                run_id=row["id"],
                number=f"{row['lane']}{row['position']:03d}",
                consignment_id=row["consignment_id"],
                vin=row["vin"],
                description=" ".join(
                    part
                    for part in (
                        str(row["model_year"]),
                        row["make"],
                        row["model"],
                        row["series"],
                    )
                    if part
                ),
                seller_id=row["seller_id"],
                buyer_id=row["buyer_id"],
                light=Light(row["light"]),
                status=RunStatus(row["status"]),
                floor=Money(row["floor_cents"]) if row["floor_cents"] is not None else None,
                hammer=Money(row["hammer_cents"]) if row["hammer_cents"] is not None else None,
            )
            for row in rows
        ]

    def require_open(self, code: str) -> Sale:
        sale = self.get(code)
        if sale.status is not SaleStatus.OPEN:
            raise PolicyViolation(
                "sale.not_open", "the sale is not running", sale=code, status=sale.status.value
            )
        return sale

    def require_run(self, code: str, number: str) -> Run:
        lane, position = number[:1].upper(), int(number[1:])
        try:
            return self.run_at(code, lane, position)
        except NotFound:
            raise NotFound("run", f"{code}/{number}") from None
