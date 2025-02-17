"""What to inspect next.

Three inspectors, two hundred units on the ground and a sale on Tuesday. The
order they work in decides how many units make the catalogue, and left to
itself the order is whatever is nearest the office.

So the queue is ordered by what it costs to leave a unit uninspected. A unit
already catalogued into Tuesday's sale is first, because an uninspected unit in
a sale is a unit that gets pulled. After that it is the ones that arrived
longest ago, because a unit sitting for a fortnight is a unit somebody has
forgotten.

A unit whose report is open but unsigned is its own problem and is called out
separately. It is not waiting for an inspector, it is waiting for the one who
already looked at it to finish.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autozest.consignment.models import Consignment, ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.inspection.service import ConditionReport, InspectionService
from autozest.storage.records import all_rows
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar


class Urgency(Enum):
    """Why this unit is where it is in the queue."""

    IN_A_SALE = "in_a_sale"
    WAITING = "waiting"
    UNFINISHED = "unfinished"

    @property
    def order(self) -> int:
        return {"in_a_sale": 0, "unfinished": 1, "waiting": 2}[self.value]


@dataclass(frozen=True)
class Waiting:
    """One unit and why it is waiting."""

    consignment: Consignment
    urgency: Urgency
    days_waiting: int
    sale_code: str | None = None
    report: ConditionReport | None = None

    @property
    def is_stale(self) -> bool:
        return self.days_waiting >= 10

    def line(self) -> str:
        where = f" for {self.sale_code}" if self.sale_code else ""
        return (
            f"{self.consignment.id}  {self.consignment.vin}  "
            f"{self.urgency.value}{where}, {self.days_waiting} days"
        )


@dataclass(frozen=True)
class Queue:
    """The whole list, in the order it should be worked."""

    waiting: tuple[Waiting, ...]
    as_at: datetime

    @property
    def count(self) -> int:
        return len(self.waiting)

    def of(self, urgency: Urgency) -> tuple[Waiting, ...]:
        return tuple(item for item in self.waiting if item.urgency is urgency)

    @property
    def stale(self) -> tuple[Waiting, ...]:
        return tuple(item for item in self.waiting if item.is_stale)

    def summary(self) -> str:
        return (
            f"{self.count} waiting, {len(self.of(Urgency.IN_A_SALE))} already in a sale, "
            f"{len(self.of(Urgency.UNFINISHED))} half done, {len(self.stale)} stale"
        )


class InspectionQueue:
    def __init__(
        self, conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
    ) -> None:
        self.conn = conn
        self.calendar = calendar or DEFAULT_CALENDAR
        self.consignments = ConsignmentRepository(conn)
        self.inspection = InspectionService(conn)

    def build(self, *, as_at: datetime | None = None) -> Queue:
        """The list, ordered by what it costs to leave each one."""
        moment = as_at or datetime.now().astimezone()
        waiting: list[Waiting] = []
        for consignment in self.consignments.with_status(ConsignmentStatus.ARRIVED):
            report = self.inspection.latest_for(consignment.id)
            urgency = Urgency.UNFINISHED if report is not None else Urgency.WAITING
            sale_code = self._sale_for(consignment.id)
            if sale_code is not None:
                urgency = Urgency.IN_A_SALE
            waiting.append(
                Waiting(
                    consignment=consignment,
                    urgency=urgency,
                    days_waiting=self._days(consignment, moment),
                    sale_code=sale_code,
                    report=report,
                )
            )
        waiting.sort(key=lambda item: (item.urgency.order, -item.days_waiting))
        return Queue(waiting=tuple(waiting), as_at=moment)

    def _sale_for(self, consignment_id: str) -> str | None:
        rows = all_rows(
            self.conn,
            "SELECT r.sale_code FROM run r JOIN sale s ON s.code = r.sale_code "
            "WHERE r.consignment_id = ? AND s.status = 'planned' LIMIT 1",
            (consignment_id,),
        )
        return rows[0]["sale_code"] if rows else None

    def _days(self, consignment: Consignment, now: datetime) -> int:
        arrived = consignment.arrived_at
        if arrived is None:
            return 0
        if arrived.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        if now < arrived:
            return 0
        return self.calendar.business_days_between(arrived.date(), now.date())

    def next_up(self, how_many: int = 10, *, as_at: datetime | None = None) -> list[Waiting]:
        """What an inspector should pick up now."""
        return list(self.build(as_at=as_at).waiting[:how_many])

    def render(self, *, as_at: datetime | None = None) -> list[str]:
        queue = self.build(as_at=as_at)
        lines = [f"inspection queue as at {queue.as_at:%Y-%m-%d %H:%M}"]
        lines.extend(f"  {item.line()}" for item in queue.waiting)
        lines.append(queue.summary())
        return lines
