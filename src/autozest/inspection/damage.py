"""The damage taxonomy, and what a line of it costs.

An inspector writes damage as a panel, a kind and a severity. Everything else
is derived, because three inspectors given the same dent will write three
different repair estimates and one of them will be a guess. The matrix here is
the published one the dealer council agreed to, so an estimate is arguable
against a table rather than against a person.

Structural is not a severity. A cracked apron at severity two is structural and
a caved door at severity five is not, and the two are announced completely
differently. That distinction is the reason this module exists at all rather
than a single integer on the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from autozest.errors import ValidationError
from autozest.money import Money

MIN_SEVERITY = 1
MAX_SEVERITY = 5


class Panel(Enum):
    """Where on the unit. Grouped by what a body shop charges to fix it."""

    FRONT_BUMPER = "front_bumper"
    REAR_BUMPER = "rear_bumper"
    HOOD = "hood"
    ROOF = "roof"
    DECK_LID = "deck_lid"
    LEFT_FRONT_DOOR = "left_front_door"
    LEFT_REAR_DOOR = "left_rear_door"
    RIGHT_FRONT_DOOR = "right_front_door"
    RIGHT_REAR_DOOR = "right_rear_door"
    LEFT_QUARTER = "left_quarter"
    RIGHT_QUARTER = "right_quarter"
    LEFT_FENDER = "left_fender"
    RIGHT_FENDER = "right_fender"
    WINDSCREEN = "windscreen"
    GLASS_OTHER = "glass_other"
    WHEEL = "wheel"
    TYRE = "tyre"
    INTERIOR = "interior"
    UNDERBODY = "underbody"
    RADIATOR_SUPPORT = "radiator_support"
    APRON = "apron"
    RAIL = "rail"
    ROCKER = "rocker"
    PILLAR = "pillar"
    FLOOR_PAN = "floor_pan"

    @property
    def is_structural_member(self) -> bool:
        """Whether damage here is structural whatever its severity."""
        return self in _STRUCTURAL_PANELS

    @property
    def is_cosmetic_only(self) -> bool:
        """Panels where even severe damage is a paint and panel job."""
        return self in _COSMETIC_PANELS


_STRUCTURAL_PANELS = frozenset(
    {
        Panel.RAIL,
        Panel.APRON,
        Panel.ROCKER,
        Panel.PILLAR,
        Panel.FLOOR_PAN,
        Panel.RADIATOR_SUPPORT,
    }
)

_COSMETIC_PANELS = frozenset(
    {
        Panel.FRONT_BUMPER,
        Panel.REAR_BUMPER,
        Panel.HOOD,
        Panel.DECK_LID,
        Panel.LEFT_FENDER,
        Panel.RIGHT_FENDER,
    }
)


class DamageKind(Enum):
    """What was done to it."""

    SCRATCH = "scratch"
    DENT = "dent"
    CREASE = "crease"
    RUST = "rust"
    HAIL = "hail"
    CRACK = "crack"
    CHIP = "chip"
    TEAR = "tear"
    BURN = "burn"
    MISSING = "missing"
    PRIOR_REPAIR = "prior_repair"
    BENT = "bent"

    @property
    def needs_replacement(self) -> bool:
        """Kinds that are never repaired, only replaced."""
        return self in (DamageKind.MISSING, DamageKind.TEAR, DamageKind.BENT)


#: Base cost of one line, before the severity multiplier, in cents.
_PANEL_BASE: dict[Panel, int] = {
    Panel.FRONT_BUMPER: 32_000,
    Panel.REAR_BUMPER: 32_000,
    Panel.HOOD: 41_000,
    Panel.ROOF: 78_000,
    Panel.DECK_LID: 38_000,
    Panel.LEFT_FRONT_DOOR: 44_000,
    Panel.LEFT_REAR_DOOR: 44_000,
    Panel.RIGHT_FRONT_DOOR: 44_000,
    Panel.RIGHT_REAR_DOOR: 44_000,
    Panel.LEFT_QUARTER: 66_000,
    Panel.RIGHT_QUARTER: 66_000,
    Panel.LEFT_FENDER: 36_000,
    Panel.RIGHT_FENDER: 36_000,
    Panel.WINDSCREEN: 29_000,
    Panel.GLASS_OTHER: 18_000,
    Panel.WHEEL: 21_000,
    Panel.TYRE: 14_000,
    Panel.INTERIOR: 24_000,
    Panel.UNDERBODY: 55_000,
    Panel.RADIATOR_SUPPORT: 88_000,
    Panel.APRON: 94_000,
    Panel.RAIL: 145_000,
    Panel.ROCKER: 82_000,
    Panel.PILLAR: 120_000,
    Panel.FLOOR_PAN: 110_000,
}

#: Multiplier per severity step, as agreed with the dealer council.
_SEVERITY_FACTOR = {1: "0.25", 2: "0.5", 3: "1.0", 4: "1.6", 5: "2.4"}

#: Kinds that cost more than the panel base suggests.
_KIND_UPLIFT = {
    DamageKind.RUST: "1.4",
    DamageKind.CRACK: "1.2",
    DamageKind.TEAR: "1.3",
    DamageKind.MISSING: "1.5",
    DamageKind.BENT: "1.35",
    DamageKind.HAIL: "0.8",
}


@dataclass(frozen=True)
class DamageLine:
    """One finding on a condition report."""

    panel: Panel
    kind: DamageKind
    severity: int
    estimate: Money | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not MIN_SEVERITY <= self.severity <= MAX_SEVERITY:
            raise ValidationError(
                f"severity {self.severity} is outside {MIN_SEVERITY} to {MAX_SEVERITY}"
            )
        if self.estimate is not None and self.estimate.is_negative:
            raise ValidationError("a repair estimate cannot be negative")

    @property
    def is_structural(self) -> bool:
        """Structural is about the panel, not about how bad it looks.

        A cracked apron at severity two is structural. A caved door at severity
        five is not. The two get completely different announcements.
        """
        return self.panel.is_structural_member

    @property
    def cost(self) -> Money:
        """The estimate if one was given, otherwise the table's answer."""
        return self.estimate if self.estimate is not None else self.table_cost()

    def table_cost(self) -> Money:
        """What the published matrix says this line costs."""
        base = Money(_PANEL_BASE[self.panel])
        line = base.times(_SEVERITY_FACTOR[self.severity])
        uplift = _KIND_UPLIFT.get(self.kind)
        return line.times(uplift) if uplift else line

    def describe(self) -> str:
        panel = self.panel.value.replace("_", " ")
        return f"{panel} {self.kind.value} severity {self.severity}"


def total_cost(lines: list[DamageLine]) -> Money:
    """What the whole report adds up to."""
    return Money(sum(line.cost.cents for line in lines))


def structural_lines(lines: list[DamageLine]) -> list[DamageLine]:
    return [line for line in lines if line.is_structural]
