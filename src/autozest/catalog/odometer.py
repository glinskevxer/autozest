"""Odometer readings and the status that gets announced with them.

Three quite different things can be wrong with a reading and they are announced
differently, so they are kept apart here rather than collapsed into a boolean:

* the reading is not the true mileage, because the cluster was swapped or the
  numbers were turned back
* the reading is true as far as it goes but the cluster has fewer digits than
  the mileage needs, so it has rolled past its limit
* the reading is legally not required to be accurate at all, because the unit
  is old enough to be exempt from the federal disclosure

The exemption rule has two halves and the model year decides which half
applies. That is not a detail: a 2009 unit is exempt after ten years and a 2011
unit is not exempt until twenty, so two units of nearly the same age get
different announcements.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from autozest.errors import PolicyViolation, ValidationError

#: The model year at which the federal exemption period changed length.
EXEMPTION_RULE_CHANGE_YEAR = 2011
EXEMPTION_YEARS_LEGACY = 10
EXEMPTION_YEARS_CURRENT = 20

#: Nothing on the block credibly reads above this without being a typo.
MAX_PLAUSIBLE_MILES = 1_500_000


class OdometerStatus(Enum):
    """What is being claimed about the reading."""

    ACTUAL = "actual"
    NOT_ACTUAL = "not_actual"
    EXCEEDS_MECHANICAL_LIMITS = "exceeds_mechanical_limits"
    EXEMPT = "exempt"

    @property
    def is_disclosable(self) -> bool:
        """True when the status has to be called at the block."""
        return self is not OdometerStatus.ACTUAL

    @property
    def blocks_mileage_claim(self) -> bool:
        """True when the number may not be advertised as the unit's mileage."""
        return self in (OdometerStatus.NOT_ACTUAL, OdometerStatus.EXEMPT)


@dataclass(frozen=True)
class Reading:
    """One odometer observation, with where it came from and when."""

    miles: int
    read_on: date
    source: str
    digits: int = 6

    def __post_init__(self) -> None:
        if self.miles < 0:
            raise ValidationError(f"an odometer cannot read {self.miles}")
        if self.miles > MAX_PLAUSIBLE_MILES:
            raise ValidationError(
                f"{self.miles} miles is past anything believable, check the entry"
            )
        if self.digits not in (5, 6, 7):
            raise ValidationError(f"a cluster with {self.digits} digits is not a thing")
        if not self.source.strip():
            raise ValidationError("a reading needs a source")

    @property
    def mechanical_limit(self) -> int:
        """The highest number this cluster can show before it rolls over."""
        return int(10**self.digits) - 1

    @property
    def is_at_limit(self) -> bool:
        return self.miles >= self.mechanical_limit


def exemption_years(model_year: int) -> int:
    """How many model years a unit has to be before the exemption applies."""
    if model_year < EXEMPTION_RULE_CHANGE_YEAR:
        return EXEMPTION_YEARS_LEGACY
    return EXEMPTION_YEARS_CURRENT


def is_exempt(model_year: int, as_of: date) -> bool:
    """Whether disclosure is not required for this unit on this date.

    Age is counted in model years against the calendar year of the sale, which
    is how the transfer paperwork counts it.
    """
    return as_of.year - model_year >= exemption_years(model_year)


def detect_rollback(
    readings: Sequence[Reading], *, cluster_replaced: bool = False
) -> int | None:
    """Return the largest backwards step in a reading history, or ``None``.

    Readings are taken in the order given, which is the order they were
    recorded rather than the order of their dates. A history that goes
    backwards is a rollback unless the cluster was replaced, in which case the
    drop is expected and the caller has said so.
    """
    if cluster_replaced:
        return None
    worst: int | None = None
    high = None
    for reading in readings:
        if high is not None and reading.miles < high:
            drop = high - reading.miles
            worst = drop if worst is None else max(worst, drop)
        high = reading.miles if high is None else max(high, reading.miles)
    return worst


def derive_status(
    readings: Sequence[Reading],
    model_year: int,
    as_of: date,
    *,
    declared: OdometerStatus | None = None,
    cluster_replaced: bool = False,
) -> OdometerStatus:
    """Work out the status to announce from the evidence.

    Order matters and it is deliberate. A rollback beats everything, because a
    unit whose numbers were turned back is not made honest by being old. The
    exemption comes next, then the mechanical limit, and only a unit that clears
    all three is announced as actual.

    A declared status is honoured when it is worse than the derived one. The
    seller is allowed to be more cautious than the evidence requires and is not
    allowed to be less.
    """
    if not readings:
        raise ValidationError("cannot derive an odometer status with no readings")

    if detect_rollback(readings, cluster_replaced=cluster_replaced) is not None:
        derived = OdometerStatus.NOT_ACTUAL
    elif cluster_replaced:
        derived = OdometerStatus.NOT_ACTUAL
    elif is_exempt(model_year, as_of):
        derived = OdometerStatus.EXEMPT
    elif any(r.is_at_limit for r in readings):
        derived = OdometerStatus.EXCEEDS_MECHANICAL_LIMITS
    else:
        derived = OdometerStatus.ACTUAL

    if declared is None:
        return derived
    return _worse_of(declared, derived)


_SEVERITY = {
    OdometerStatus.ACTUAL: 0,
    OdometerStatus.EXEMPT: 1,
    OdometerStatus.EXCEEDS_MECHANICAL_LIMITS: 2,
    OdometerStatus.NOT_ACTUAL: 3,
}


def _worse_of(left: OdometerStatus, right: OdometerStatus) -> OdometerStatus:
    return left if _SEVERITY[left] >= _SEVERITY[right] else right


def check_declaration(declared: OdometerStatus, derived: OdometerStatus) -> None:
    """Raise when a seller has declared a status softer than the evidence."""
    if _SEVERITY[declared] < _SEVERITY[derived]:
        raise PolicyViolation(
            "odometer.declaration",
            "the declared odometer status is softer than the evidence supports",
            declared=declared.value,
            derived=derived.value,
        )
