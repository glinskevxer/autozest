"""What a buyer may come back on, and for how long.

Every number in this module was argued over with the dealer council and every
one of them moves. That is why a claim stores the version it was opened under:
policy changes in January, and a claim opened in December has to be decided on
December's rules two years later when nobody remembers what they were.

The shape of the policy is three separate gates and they are not the same kind
of thing.

**The window** is time, and it is counted in whichever unit the class deserves.
Forty eight business hours for a gearbox, because a buyer who drives the unit
knows within a day. Ninety calendar days for a rolled odometer, because nobody
finds that by driving it.

**The threshold** is money. Under it the claim costs more to handle than the
unit is worth, so it is not heard at all. It is measured against the hammer
price, which is the number that can move later when a price adjustment lands on
some other claim against the same unit.

**The light** is what the buyer was told. Green hears everything. Yellow hears
only what an inspection on the lot could not have found, which is exactly the
:attr:`~autozest.defects.DefectClass.survives_inspection` property. Blue is a
title lane and hears title matters. Red heard "as is" and hears almost nothing,
with one exception that is not negotiable: a unit sold as-is still has to come
with a title that is what it was said to be.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from autozest.defects import DefectClass
from autozest.errors import ConfigError
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.timing import Window, WindowUnit

#: Bump this whenever any number below changes.
POLICY_VERSION = "2024.1"

#: Money is immutable so a shared instance is safe as a default.
ZERO = Money.zero()

_HOURS = WindowUnit.BUSINESS_HOURS
_BDAYS = WindowUnit.BUSINESS_DAYS
_CDAYS = WindowUnit.CALENDAR_DAYS


@dataclass(frozen=True)
class ClaimRule:
    """The window and threshold for one class of defect."""

    window: Window
    threshold: Money = ZERO
    needs_evidence: bool = False

    @property
    def is_arbitrable(self) -> bool:
        """A window of nothing means the class is never heard."""
        return self.window.amount > 0


DEFAULT_RULES: dict[DefectClass, ClaimRule] = {
    DefectClass.STRUCTURAL: ClaimRule(Window(7, _BDAYS), needs_evidence=True),
    DefectClass.ODOMETER: ClaimRule(Window(90, _CDAYS), needs_evidence=True),
    DefectClass.TITLE: ClaimRule(Window(30, _CDAYS)),
    DefectClass.BRANDING: ClaimRule(Window(180, _CDAYS), needs_evidence=True),
    DefectClass.ENGINE: ClaimRule(Window(48, _HOURS), Money.parse("3500.00"), True),
    DefectClass.TRANSMISSION: ClaimRule(Window(48, _HOURS), Money.parse("3500.00"), True),
    DefectClass.DRIVE_AXLE: ClaimRule(Window(48, _HOURS), Money.parse("3500.00"), True),
    DefectClass.ELECTRICAL: ClaimRule(Window(24, _HOURS), Money.parse("3500.00")),
    DefectClass.SUSPENSION: ClaimRule(Window(24, _HOURS), Money.parse("5000.00")),
    DefectClass.EMISSIONS: ClaimRule(Window(14, _CDAYS)),
    DefectClass.AIRBAG: ClaimRule(Window(7, _BDAYS), needs_evidence=True),
    DefectClass.FLOOD: ClaimRule(Window(90, _CDAYS), needs_evidence=True),
    DefectClass.PRIOR_USE: ClaimRule(Window(30, _CDAYS)),
    DefectClass.MISSING_EQUIPMENT: ClaimRule(Window(24, _HOURS), Money.parse("2000.00")),
    # Cosmetic damage was there on the lot to be looked at.
    DefectClass.BODY_COSMETIC: ClaimRule(Window(0, _CDAYS)),
}

#: What each light will hear at all. Yellow is derived, not listed.
_RED_HEARS = frozenset({DefectClass.TITLE, DefectClass.BRANDING})
_BLUE_HEARS = frozenset({DefectClass.TITLE, DefectClass.BRANDING, DefectClass.ODOMETER})


def classes_for(light: Light) -> frozenset[DefectClass]:
    """Which classes a unit under this light can be claimed on."""
    if light is Light.GREEN:
        return frozenset(DefectClass)
    if light is Light.YELLOW:
        return frozenset(c for c in DefectClass if c.survives_inspection)
    if light is Light.BLUE:
        return _BLUE_HEARS
    return _RED_HEARS


@dataclass(frozen=True)
class ArbitrationPolicy:
    """One version of the rules."""

    version: str = POLICY_VERSION
    rules: Mapping[DefectClass, ClaimRule] = field(default_factory=lambda: dict(DEFAULT_RULES))

    def rule_for(self, defect: DefectClass) -> ClaimRule:
        try:
            return self.rules[defect]
        except KeyError as exc:
            raise ConfigError(
                f"policy {self.version} has no rule for {defect.value}", version=self.version
            ) from exc

    def hears(self, light: Light, defect: DefectClass) -> bool:
        """Whether this light hears this class at all, before any timing."""
        return defect in classes_for(light) and self.rule_for(defect).is_arbitrable

    def window_for(self, defect: DefectClass) -> Window:
        return self.rule_for(defect).window

    def threshold_for(self, defect: DefectClass) -> Money:
        return self.rule_for(defect).threshold


def from_config(
    section: Mapping[str, Any], base: ArbitrationPolicy | None = None
) -> ArbitrationPolicy:
    """Overlay a configuration section on top of the shipped policy.

    Only the classes named are touched. A file that mentions the gearbox window
    and nothing else leaves every other class exactly as it shipped, which is
    how the council's amendments actually arrive.
    """
    policy = base or ArbitrationPolicy()
    rules = dict(policy.rules)
    version = str(section.get("version", policy.version))

    for key, value in section.items():
        if key == "version":
            continue
        try:
            defect = DefectClass(key)
        except ValueError as exc:
            raise ConfigError(
                f"{key!r} is not a defect class", known=[c.value for c in DefectClass]
            ) from exc
        if not isinstance(value, dict):
            raise ConfigError(f"[arbitration.{key}] should be a table")
        rules[defect] = _rule_from(value, rules[defect], key)

    return ArbitrationPolicy(version=version, rules=rules)


def _rule_from(value: Mapping[str, Any], current: ClaimRule, key: str) -> ClaimRule:
    amount = value.get("window", current.window.amount)
    unit_text = value.get("unit", current.window.unit.value)
    try:
        unit = WindowUnit(unit_text)
    except ValueError as exc:
        raise ConfigError(
            f"arbitration.{key}.unit is not a window unit: {unit_text!r}",
            known=[u.value for u in WindowUnit],
        ) from exc
    threshold = value.get("threshold")
    return ClaimRule(
        window=Window(int(amount), unit),
        threshold=Money.parse(str(threshold)) if threshold is not None else current.threshold,
        needs_evidence=bool(value.get("needs_evidence", current.needs_evidence)),
    )
