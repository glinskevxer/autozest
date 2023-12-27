"""The unit itself: what it is, and what its odometer has said over time.

A vehicle record outlives any one consignment. The same truck comes back
through the lanes three times in two years, and the value of keeping it is
exactly that the readings from all three visits sit in one history. A rollback
between visits is invisible to anyone looking at a single sale.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from autozest.catalog.odometer import OdometerStatus, Reading, derive_status
from autozest.errors import Conflict, ValidationError
from autozest.storage.records import all_rows, insert, now_text, one, one_or_raise, scalar
from autozest.vin import Vin


@dataclass(frozen=True)
class Vehicle:
    """One unit, keyed on its VIN."""

    vin: Vin
    make: str
    model: str
    series: str | None = None
    body_style: str | None = None
    drivetrain: str | None = None
    fuel: str | None = None
    exterior: str | None = None
    interior: str | None = None
    model_year: int | None = None
    check_digit_ok: bool = True

    @property
    def year(self) -> int:
        """The model year, taken from the VIN unless one was given."""
        if self.model_year is not None:
            return self.model_year
        return self.vin.model_year

    @property
    def description(self) -> str:
        """The one line a lot card and a block screen both carry."""
        parts = [str(self.year), self.make, self.model]
        if self.series:
            parts.append(self.series)
        return " ".join(parts)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Vehicle:
        return cls(
            vin=Vin.parse(row["vin"], require_check_digit=False),
            make=row["make"],
            model=row["model"],
            series=row["series"],
            body_style=row["body_style"],
            drivetrain=row["drivetrain"],
            fuel=row["fuel"],
            exterior=row["exterior"],
            interior=row["interior"],
            model_year=row["model_year"],
            check_digit_ok=bool(row["check_digit_ok"]),
        )


@dataclass(frozen=True)
class OdometerHistory:
    """Every reading recorded against a VIN, oldest first."""

    vin: str
    readings: list[Reading] = field(default_factory=list)

    @property
    def latest(self) -> Reading | None:
        return self.readings[-1] if self.readings else None

    @property
    def highest(self) -> Reading | None:
        return max(self.readings, key=lambda r: r.miles) if self.readings else None

    def status(
        self, model_year: int, as_of: date, *, cluster_replaced: bool = False
    ) -> OdometerStatus:
        return derive_status(
            self.readings, model_year, as_of, cluster_replaced=cluster_replaced
        )


class VehicleRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, vehicle: Vehicle) -> Vehicle:
        """Record a unit. A VIN already on file is a conflict, not an update."""
        insert(
            self.conn,
            "vehicle",
            {
                "vin": vehicle.vin.value,
                "model_year": vehicle.year,
                "make": vehicle.make,
                "model": vehicle.model,
                "series": vehicle.series,
                "body_style": vehicle.body_style,
                "drivetrain": vehicle.drivetrain,
                "fuel": vehicle.fuel,
                "exterior": vehicle.exterior,
                "interior": vehicle.interior,
                "check_digit_ok": int(vehicle.vin.check_digit_matches),
                "created_at": now_text(),
            },
        )
        return vehicle

    def get(self, vin: str | Vin) -> Vehicle:
        key = str(vin)
        row = one_or_raise(
            self.conn, "SELECT * FROM vehicle WHERE vin = ?", (key,), "vehicle", key
        )
        return Vehicle.from_row(row)

    def find(self, vin: str | Vin) -> Vehicle | None:
        row = one(self.conn, "SELECT * FROM vehicle WHERE vin = ?", (str(vin),))
        return Vehicle.from_row(row) if row else None

    def upsert(self, vehicle: Vehicle) -> Vehicle:
        """Add the unit, or return the one already on file untouched."""
        existing = self.find(vehicle.vin)
        if existing is not None:
            return existing
        return self.add(vehicle)

    def record_reading(self, vin: str | Vin, reading: Reading) -> None:
        """Append a reading. The unit has to be on file first."""
        key = str(vin)
        if not scalar(self.conn, "SELECT 1 FROM vehicle WHERE vin = ? LIMIT 1", (key,)):
            raise ValidationError(f"no vehicle on file for {key}", vin=key)
        insert(
            self.conn,
            "odometer_reading",
            {
                "vin": key,
                "miles": reading.miles,
                "read_on": reading.read_on.isoformat(),
                "source": reading.source,
                "digits": reading.digits,
                "recorded_at": now_text(),
            },
        )

    def history(self, vin: str | Vin) -> OdometerHistory:
        """The reading history in the order it was recorded."""
        key = str(vin)
        rows = all_rows(
            self.conn,
            "SELECT miles, read_on, source, digits FROM odometer_reading "
            "WHERE vin = ? ORDER BY id",
            (key,),
        )
        readings = [
            Reading(
                miles=row["miles"],
                read_on=date.fromisoformat(row["read_on"]),
                source=row["source"],
                digits=row["digits"],
            )
            for row in rows
        ]
        return OdometerHistory(vin=key, readings=readings)

    def search(
        self, *, make: str | None = None, model_year: int | None = None
    ) -> list[Vehicle]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM vehicle WHERE (? IS NULL OR make = ?) "
            "AND (? IS NULL OR model_year = ?) ORDER BY model_year DESC, make, model",
            (make, make, model_year, model_year),
        )
        return [Vehicle.from_row(row) for row in rows]

    def require_absent(self, vin: str | Vin) -> None:
        if self.find(vin) is not None:
            raise Conflict("that vehicle is already on file", vin=str(vin))
