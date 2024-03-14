"""Sales, lanes and runs.

A run is the join between a consignment and a sale, and it is where the light
lives. That is deliberate. The light is decided once, when the unit is
catalogued, and it never moves afterwards. An arbitration six weeks later asks
what light the unit ran under, and recomputing it from today's announcement set
would answer a different question, because anything called after the sale would
be folded in.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.errors import StateError, ValidationError
from autozest.inspection.announcements import Light
from autozest.money import Money


class SaleStatus(Enum):
    """Where the sale is in its own day."""

    PLANNED = "planned"
    OPEN = "open"
    CLOSED = "closed"
    SETTLED = "settled"

    @property
    def accepts_catalogue_changes(self) -> bool:
        """Whether units can still be added to or moved around the run order."""
        return self is SaleStatus.PLANNED

    @property
    def is_finished(self) -> bool:
        return self in (SaleStatus.CLOSED, SaleStatus.SETTLED)


class RunStatus(Enum):
    """What happened when the unit crossed."""

    SCHEDULED = "scheduled"
    SOLD = "sold"
    NO_SALE = "no_sale"
    IF_BID = "if_bid"
    PULLED = "pulled"

    @property
    def is_settled(self) -> bool:
        """Whether the outcome is final for the night."""
        return self in (RunStatus.SOLD, RunStatus.NO_SALE, RunStatus.PULLED)


SALE_TRANSITIONS: dict[SaleStatus, frozenset[SaleStatus]] = {
    SaleStatus.PLANNED: frozenset({SaleStatus.OPEN}),
    SaleStatus.OPEN: frozenset({SaleStatus.CLOSED}),
    SaleStatus.CLOSED: frozenset({SaleStatus.SETTLED}),
    SaleStatus.SETTLED: frozenset(),
}

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.SCHEDULED: frozenset(
        {RunStatus.SOLD, RunStatus.NO_SALE, RunStatus.IF_BID, RunStatus.PULLED}
    ),
    RunStatus.IF_BID: frozenset({RunStatus.SOLD, RunStatus.NO_SALE}),
    RunStatus.SOLD: frozenset(),
    RunStatus.NO_SALE: frozenset(),
    RunStatus.PULLED: frozenset(),
}


@dataclass(frozen=True)
class Sale:
    """One sale day."""

    code: str
    sale_date: date
    title: str
    status: SaleStatus = SaleStatus.PLANNED
    opened_at: datetime | None = None
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValidationError("a sale needs a title")

    def with_status(self, status: SaleStatus) -> Sale:
        if status not in SALE_TRANSITIONS[self.status]:
            allowed = (
                ", ".join(sorted(s.value for s in SALE_TRANSITIONS[self.status])) or "nothing"
            )
            raise StateError(f"sale {self.code}", self.status.value, allowed)
        return replace(self, status=status)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Sale:
        return cls(
            code=row["code"],
            sale_date=date.fromisoformat(row["sale_date"]),
            title=row["title"],
            status=SaleStatus(row["status"]),
            opened_at=_time(row["opened_at"]),
            closed_at=_time(row["closed_at"]),
        )


@dataclass(frozen=True)
class Lane:
    """One lane of one sale."""

    sale_code: str
    letter: str
    capacity: int
    auctioneer: str | None = None

    def __post_init__(self) -> None:
        if len(self.letter) != 1 or not self.letter.isalpha():
            raise ValidationError(f"{self.letter!r} is not a lane letter")
        if self.capacity < 1:
            raise ValidationError(f"a lane cannot hold {self.capacity} units")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Lane:
        return cls(
            sale_code=row["sale_code"],
            letter=row["letter"],
            capacity=row["capacity"],
            auctioneer=row["auctioneer"],
        )


@dataclass(frozen=True)
class Run:
    """One unit in one lane at one position, and how it went."""

    id: int | None
    sale_code: str
    lane: str
    position: int
    consignment_id: str
    light: Light
    status: RunStatus = RunStatus.SCHEDULED
    hammer: Money | None = None
    buyer_id: str | None = None
    ran_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.position < 1:
            raise ValidationError(f"{self.position} is not a run position")
        if self.status is RunStatus.SOLD and self.hammer is None:
            raise ValidationError("a sold run needs a hammer price")
        if self.status is RunStatus.SOLD and not self.buyer_id:
            raise ValidationError("a sold run needs a buyer")

    @property
    def number(self) -> str:
        return f"{self.lane.upper()}{self.position:03d}"

    @property
    def arbitration_is_possible(self) -> bool:
        """Red is as is. Everything else leaves something on the table."""
        return self.light is not Light.RED

    def with_status(self, status: RunStatus) -> Run:
        if status not in RUN_TRANSITIONS[self.status]:
            allowed = (
                ", ".join(sorted(s.value for s in RUN_TRANSITIONS[self.status])) or "nothing"
            )
            raise StateError(f"run {self.number}", self.status.value, allowed)
        return replace(self, status=status)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Run:
        return cls(
            id=row["id"],
            sale_code=row["sale_code"],
            lane=row["lane"],
            position=row["position"],
            consignment_id=row["consignment_id"],
            light=Light(row["light"]),
            status=RunStatus(row["status"]),
            hammer=Money(row["hammer_cents"]) if row["hammer_cents"] is not None else None,
            buyer_id=row["buyer_id"],
            ran_at=_time(row["ran_at"]),
        )


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
