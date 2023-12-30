"""The consignment itself and the states it moves through.

A consignment is one unit offered by one seller for one run through the lanes.
The vehicle record outlives it. The consignment is closed the moment the unit
leaves, and the same VIN can be consigned again next month.

The state machine is written out rather than implied by a pile of booleans,
because the interesting questions are all about which state a unit was in when
something happened. An arbitration months later asks what the unit was when it
crossed the block, and a boolean that has since been flipped cannot answer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any

from autozest.errors import StateError, ValidationError
from autozest.money import Money


class ConsignmentStatus(Enum):
    """Where a unit is in its trip through the sale."""

    REGISTERED = "registered"
    ARRIVED = "arrived"
    INSPECTED = "inspected"
    CATALOGUED = "catalogued"
    SOLD = "sold"
    NO_SALE = "no_sale"
    WITHDRAWN = "withdrawn"
    RELEASED = "released"

    @property
    def is_open(self) -> bool:
        """Whether the unit still occupies the vin as a live consignment."""
        return self not in (
            ConsignmentStatus.SOLD,
            ConsignmentStatus.WITHDRAWN,
            ConsignmentStatus.RELEASED,
        )

    @property
    def is_on_the_ground(self) -> bool:
        """Whether the unit is physically here."""
        return self in (
            ConsignmentStatus.ARRIVED,
            ConsignmentStatus.INSPECTED,
            ConsignmentStatus.CATALOGUED,
            ConsignmentStatus.NO_SALE,
        )


#: The only moves allowed. Anything not listed here raises.
TRANSITIONS: dict[ConsignmentStatus, frozenset[ConsignmentStatus]] = {
    ConsignmentStatus.REGISTERED: frozenset(
        {ConsignmentStatus.ARRIVED, ConsignmentStatus.WITHDRAWN}
    ),
    ConsignmentStatus.ARRIVED: frozenset(
        {ConsignmentStatus.INSPECTED, ConsignmentStatus.WITHDRAWN}
    ),
    ConsignmentStatus.INSPECTED: frozenset(
        {ConsignmentStatus.CATALOGUED, ConsignmentStatus.WITHDRAWN}
    ),
    ConsignmentStatus.CATALOGUED: frozenset(
        {
            ConsignmentStatus.SOLD,
            ConsignmentStatus.NO_SALE,
            ConsignmentStatus.WITHDRAWN,
        }
    ),
    ConsignmentStatus.NO_SALE: frozenset(
        {
            ConsignmentStatus.CATALOGUED,
            ConsignmentStatus.RELEASED,
            ConsignmentStatus.WITHDRAWN,
        }
    ),
    ConsignmentStatus.SOLD: frozenset(),
    ConsignmentStatus.WITHDRAWN: frozenset(),
    ConsignmentStatus.RELEASED: frozenset(),
}


def require_transition(current: ConsignmentStatus, wanted: ConsignmentStatus) -> None:
    """Raise unless the move is one the state machine allows."""
    if wanted not in TRANSITIONS[current]:
        allowed = ", ".join(sorted(s.value for s in TRANSITIONS[current])) or "nothing"
        raise StateError(f"a {current.value} consignment", current.value, allowed)


@dataclass(frozen=True)
class Consignment:
    """One unit, one seller, one trip through the lanes."""

    id: str
    vin: str
    seller_id: str
    status: ConsignmentStatus = ConsignmentStatus.REGISTERED
    floor: Money | None = None
    floor_set_at: datetime | None = None
    keys_count: int = 0
    gate_pass: str | None = None
    transport_ref: str | None = None
    title_expected: bool = True
    arrived_at: datetime | None = None
    closed_at: datetime | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValidationError("a consignment needs an id")
        if not 0 <= self.keys_count <= 6:
            raise ValidationError(f"{self.keys_count} keys is not a believable count")
        if self.floor is not None and self.floor.is_negative:
            raise ValidationError("a floor cannot be negative", floor=str(self.floor))

    @property
    def has_floor(self) -> bool:
        return self.floor is not None

    @property
    def is_open(self) -> bool:
        return self.status.is_open

    def with_status(self, status: ConsignmentStatus) -> Consignment:
        """A copy in the new status, checked against the state machine."""
        require_transition(self.status, status)
        return replace(self, status=status)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Consignment:
        return cls(
            id=row["id"],
            vin=row["vin"],
            seller_id=row["seller_id"],
            status=ConsignmentStatus(row["status"]),
            floor=Money(row["floor_cents"]) if row["floor_cents"] is not None else None,
            floor_set_at=_time(row["floor_set_at"]),
            keys_count=row["keys_count"],
            gate_pass=row["gate_pass"],
            transport_ref=row["transport_ref"],
            title_expected=bool(row["title_expected"]),
            arrived_at=_time(row["arrived_at"]),
            closed_at=_time(row["closed_at"]),
            note=row["note"],
        )


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
