"""What a unit is worth, and what to tell a seller to ask.

Two sources. A guide bought in from a third party, which is a starting point
and nothing more, and what the lanes here actually paid, which is better
evidence and free.

The advice prefers our own sales once there are enough of them. Three
comparable units is the threshold and it is deliberately low: three real
hammers from this room beat a national guide that has never seen our buyers.

A floor is advised at a discount to the value rather than at it. A seller who
asks exactly what a unit is worth gets a no sale and a rerun fee, because the
room has to have somewhere to go.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from autozest.catalog.vehicles import Vehicle
from autozest.errors import ValidationError
from autozest.inspection.grade import GradeBand
from autozest.money import Money
from autozest.storage.records import all_rows, insert

#: How many of our own sales before we stop looking at the guide.
ENOUGH_COMPARABLES = 3

#: How far back a sale still counts as comparable.
LOOKBACK_DAYS = 120

#: What to advise as a floor, against the value.
FLOOR_DISCOUNT = Decimal("0.88")


@dataclass(frozen=True)
class Comparable:
    """One unit like this one that actually sold."""

    consignment_id: str
    hammer: Money
    sold_on: date
    grade_band: GradeBand | None = None


@dataclass(frozen=True)
class Advice:
    """What to tell a seller, and where the number came from."""

    value: Money
    source: str
    samples: int
    band: GradeBand | None = None

    @property
    def suggested_floor(self) -> Money:
        return self.value.times(FLOOR_DISCOUNT)

    @property
    def is_from_our_lanes(self) -> bool:
        return self.source == "lanes"

    def explain(self) -> str:
        if self.samples == 0:
            return "nothing to go on"
        where = "our lanes" if self.is_from_our_lanes else self.source
        return (
            f"{self.value} from {self.samples} of {where}, "
            f"ask a floor around {self.suggested_floor}"
        )


class MarketService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add_guide(
        self,
        make: str,
        model: str,
        model_year: int,
        band: GradeBand,
        value: Money,
        *,
        source: str = "guide",
        as_at: date | None = None,
    ) -> None:
        if value.is_negative:
            raise ValidationError("a guide value cannot be negative")
        insert(
            self.conn,
            "guide_value",
            {
                "make": make,
                "model": model,
                "model_year": model_year,
                "grade_band": band.value,
                "value_cents": value.cents,
                "source": source,
                "as_at": (as_at or date.today()).isoformat(),
            },
        )

    def guide(self, make: str, model: str, model_year: int, band: GradeBand) -> Money | None:
        rows = all_rows(
            self.conn,
            "SELECT value_cents FROM guide_value WHERE make = ? AND model = ? "
            "AND model_year = ? AND grade_band = ? ORDER BY as_at DESC LIMIT 1",
            (make, model, model_year, band.value),
        )
        return Money(rows[0]["value_cents"]) if rows else None

    def comparables(
        self, vehicle: Vehicle, *, as_at: date | None = None, years: int = 1
    ) -> list[Comparable]:
        """Units like this one that sold here recently.

        Like means the same make and model within a year either way, which is
        how a buyer looks at it. Trim levels vary more than model years do.
        """
        today = as_at or date.today()
        since = today - timedelta(days=LOOKBACK_DAYS)
        rows = all_rows(
            self.conn,
            "SELECT r.consignment_id, r.hammer_cents, s.sale_date "
            "FROM run r JOIN sale s ON s.code = r.sale_code "
            "JOIN consignment c ON c.id = r.consignment_id "
            "JOIN vehicle v ON v.vin = c.vin "
            "WHERE r.status = 'sold' AND v.make = ? AND v.model = ? "
            "AND v.model_year BETWEEN ? AND ? AND s.sale_date >= ? AND s.sale_date <= ? "
            "ORDER BY s.sale_date DESC",
            (
                vehicle.make,
                vehicle.model,
                vehicle.year - years,
                vehicle.year + years,
                since.isoformat(),
                today.isoformat(),
            ),
        )
        return [
            Comparable(
                consignment_id=row["consignment_id"],
                hammer=Money(row["hammer_cents"]),
                sold_on=date.fromisoformat(row["sale_date"]),
            )
            for row in rows
        ]

    def advise(self, vehicle: Vehicle, band: GradeBand, *, as_at: date | None = None) -> Advice:
        """What to tell the seller to ask."""
        sold = self.comparables(vehicle, as_at=as_at)
        if len(sold) >= ENOUGH_COMPARABLES:
            middle = int(median(sorted(item.hammer.cents for item in sold)))
            return Advice(value=Money(middle), source="lanes", samples=len(sold), band=band)
        guide = self.guide(vehicle.make, vehicle.model, vehicle.year, band)
        if guide is not None:
            return Advice(value=guide, source="guide", samples=1, band=band)
        if sold:
            middle = int(median(sorted(item.hammer.cents for item in sold)))
            return Advice(value=Money(middle), source="lanes", samples=len(sold), band=band)
        return Advice(value=Money.zero(), source="nothing", samples=0, band=band)
