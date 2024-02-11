"""Turning a list of findings into the number on the front of the report.

The grade runs from 0.0 to 5.0 and it is the single most quoted figure in the
building. A buyer three states away bids on it without seeing the unit, so it
has to fall out of the findings rather than out of an opinion.

Two things happen, in this order. First the repair total is subtracted against
a reference value, which is the ordinary case. Then the caps are applied, and
the caps are absolute: a unit with a bent rail cannot be graded above a three
however cheap the repair estimate came out, and a unit that will not start
cannot be graded above a one however tidy it looks. A cap is not a deduction
and does not stack. The lowest applicable cap wins outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from autozest.errors import ValidationError
from autozest.inspection.damage import DamageLine, structural_lines, total_cost
from autozest.money import Money

TOP_GRADE = Decimal("5.0")
BOTTOM_GRADE = Decimal("0.0")

#: The value a repair total is measured against when nobody supplies one.
DEFAULT_REFERENCE_VALUE = Money.parse("12000.00")

#: How many grade points a repair bill equal to the reference value costs.
POINTS_PER_REFERENCE = Decimal("8.0")

#: Caps, applied after the deduction. The lowest that applies wins.
STRUCTURAL_CAP = Decimal("3.0")
NOT_DRIVABLE_CAP = Decimal("2.0")
NO_START_CAP = Decimal("1.0")
BALD_TYRE_CAP = Decimal("3.9")

#: Tread at or below this is called out and caps the grade.
BALD_TREAD_32NDS = 3


class GradeBand(Enum):
    """What the grade is called when it is spoken about."""

    EXTRA_CLEAN = "extra_clean"
    CLEAN = "clean"
    AVERAGE = "average"
    ROUGH = "rough"
    SALVAGE = "salvage"

    @classmethod
    def of(cls, grade: Decimal) -> GradeBand:
        if grade >= Decimal("4.0"):
            return cls.EXTRA_CLEAN
        if grade >= Decimal("3.0"):
            return cls.CLEAN
        if grade >= Decimal("2.0"):
            return cls.AVERAGE
        if grade >= Decimal("1.0"):
            return cls.ROUGH
        return cls.SALVAGE


@dataclass(frozen=True)
class GradeResult:
    """The grade and, more usefully, why it came out that way."""

    grade: Decimal
    uncapped: Decimal
    repair_total: Money
    caps_applied: tuple[str, ...]

    @property
    def band(self) -> GradeBand:
        return GradeBand.of(self.grade)

    @property
    def was_capped(self) -> bool:
        return bool(self.caps_applied)

    def explain(self) -> str:
        if not self.caps_applied:
            return f"{self.grade} from {self.repair_total} of repairs"
        reasons = ", ".join(self.caps_applied)
        return f"{self.grade} capped by {reasons}, would have been {self.uncapped}"


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def compute(
    lines: list[DamageLine],
    *,
    drivable: bool = True,
    engine_starts: bool = True,
    tread_32nds: int | None = None,
    reference_value: Money | None = None,
) -> GradeResult:
    """Grade a unit from its findings.

    ``reference_value`` is what the unit would be worth clean. A two thousand
    dollar repair on a forty thousand dollar truck is not the same finding as
    the same repair on an eight thousand dollar hatchback, and grading them the
    same is how a clean truck ends up in the rough lane.
    """
    # Money is falsy at zero, so this cannot be an `or`. A caller passing a
    # reference of zero means it, and means something wrong.
    reference = DEFAULT_REFERENCE_VALUE if reference_value is None else reference_value
    if reference.cents <= 0:
        raise ValidationError("the reference value has to be more than zero")
    if tread_32nds is not None and not 0 <= tread_32nds <= 12:
        raise ValidationError(f"{tread_32nds}/32 is not a tread depth")

    repairs = total_cost(lines)
    ratio = Decimal(repairs.cents) / Decimal(reference.cents)
    uncapped = _round(max(BOTTOM_GRADE, TOP_GRADE - ratio * POINTS_PER_REFERENCE))

    caps: list[tuple[str, Decimal]] = []
    if structural_lines(lines):
        caps.append(("structural damage", STRUCTURAL_CAP))
    if not drivable:
        caps.append(("not drivable", NOT_DRIVABLE_CAP))
    if not engine_starts:
        caps.append(("will not start", NO_START_CAP))
    if tread_32nds is not None and tread_32nds <= BALD_TREAD_32NDS:
        caps.append(("tyres at the wear bars", BALD_TYRE_CAP))

    grade = uncapped
    applied: list[str] = []
    for reason, cap in caps:
        if uncapped > cap:
            applied.append(reason)
        grade = min(grade, cap)

    return GradeResult(
        grade=_round(grade),
        uncapped=uncapped,
        repair_total=repairs,
        caps_applied=tuple(applied),
    )
