"""Forms that have to exist on paper.

Two of them, and both are federal requirements rather than anything this
business invented. They are generated from the record rather than typed,
because a form that disagrees with the system is worse than no form: it is
evidence, signed, that says something the file does not.

The odometer disclosure is the one that matters. It has to carry the reading,
the status, both parties and a signature line, and the status wording is fixed
by statute rather than by us. Getting creative with it is how a disclosure
stops being one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from autozest.catalog.odometer import OdometerStatus
from autozest.catalog.vehicles import Vehicle
from autozest.errors import ValidationError
from autozest.money import Money
from autozest.reporting.text import heading, wrap

#: Statutory wording. Not ours to reword.
STATUS_WORDING = {
    OdometerStatus.ACTUAL: (
        "I certify to the best of my knowledge that the odometer reading is the "
        "actual mileage of the vehicle."
    ),
    OdometerStatus.EXCEEDS_MECHANICAL_LIMITS: (
        "I certify to the best of my knowledge that the odometer reading reflects "
        "the amount of mileage in excess of its mechanical limits."
    ),
    OdometerStatus.NOT_ACTUAL: (
        "I certify to the best of my knowledge that the odometer reading is NOT "
        "the actual mileage. WARNING: ODOMETER DISCREPANCY."
    ),
    OdometerStatus.EXEMPT: (
        "This vehicle is exempt from federal odometer disclosure requirements."
    ),
}

WIDTH = 88


@dataclass(frozen=True)
class Party:
    """A name and an address on a form."""

    name: str
    address: str
    licence: str | None = None

    def lines(self, label: str) -> list[str]:
        rows = [f"{label}: {self.name}", f"  {self.address}"]
        if self.licence:
            rows.append(f"  dealer licence {self.licence}")
        return rows


def odometer_disclosure(
    vehicle: Vehicle,
    miles: int,
    status: OdometerStatus,
    seller: Party,
    buyer: Party,
    *,
    on: date | None = None,
) -> list[str]:
    """The federal odometer disclosure for one unit."""
    if miles < 0:
        raise ValidationError("an odometer disclosure cannot show negative miles")
    day = on or date.today()
    lines = heading("ODOMETER DISCLOSURE STATEMENT", width=WIDTH)
    lines.append("")
    lines.extend(
        wrap(
            "Federal law and State law, if applicable, require that you state the "
            "mileage upon transfer of ownership. Failure to complete or providing a "
            "false statement may result in fines or imprisonment.",
            WIDTH,
        )
    )
    lines.append("")
    lines.append(f"vehicle       {vehicle.description}")
    lines.append(f"vin           {vehicle.vin}")
    lines.append(f"odometer      {miles:,} miles")
    lines.append("")
    lines.extend(wrap(STATUS_WORDING[status], WIDTH))
    lines.append("")
    lines.extend(seller.lines("transferor"))
    lines.append("  signature ______________________________  date ____________")
    lines.append("")
    lines.extend(buyer.lines("transferee"))
    lines.append("  signature ______________________________  date ____________")
    lines.append("")
    lines.append(f"prepared {day.isoformat()}")
    return lines


def bill_of_sale(
    vehicle: Vehicle,
    price: Money,
    seller: Party,
    buyer: Party,
    *,
    announcements: list[str] | None = None,
    on: date | None = None,
) -> list[str]:
    """A bill of sale carrying whatever was called at the block."""
    if price.is_negative:
        raise ValidationError("a bill of sale cannot show a negative price")
    day = on or date.today()
    lines = heading("BILL OF SALE", width=WIDTH)
    lines.append("")
    lines.append(f"vehicle       {vehicle.description}")
    lines.append(f"vin           {vehicle.vin}")
    lines.append(f"price         {price}")
    lines.append(f"date          {day.isoformat()}")
    lines.append("")
    called = announcements or []
    lines.append("announced at the block")
    if called:
        lines.extend(f"  {item}" for item in called)
    else:
        lines.append("  nothing")
    lines.append("")
    lines.extend(
        wrap(
            "The vehicle is sold on the terms announced at the block and recorded "
            "above. Nothing announced above is subject to arbitration.",
            WIDTH,
        )
    )
    lines.append("")
    lines.extend(seller.lines("seller"))
    lines.append("  signature ______________________________")
    lines.append("")
    lines.extend(buyer.lines("buyer"))
    lines.append("  signature ______________________________")
    return lines
