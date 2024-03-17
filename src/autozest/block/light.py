"""Deciding which light a unit runs under.

The light is the single most consequential number in the building after the
hammer price. It decides what a buyer can come back on, so getting it wrong in
either direction costs real money: too green and the auction eats a buy back it
never agreed to, too red and the unit brings four hundred dollars less than it
should have.

Four separate things push the light, and they do not push the same way.

* **Announcements** force a light and cannot be argued with. Frame damage is
  yellow, as-is is red, and a missing title is blue. This is a floor on
  restriction, never a ceiling.
* **The grade** pushes a rough unit down. A unit under a two runs red whatever
  the announcements say, because at that grade the buyer is buying a project.
* **The money** pushes cheap units down. Under the as-is threshold nothing is
  arbitrated, because the cost of processing a claim exceeds the unit.
* **The seller class** pulls in both directions. A factory unit gets green on
  the strength of the manufacturer standing behind it, and a repossession
  never does better than yellow because the finance company has never seen it.

The interesting part is that a mechanical guarantee relaxes the grade rule and
the money rule, and does nothing at all to the announcement rule. A seller can
buy back the arbitration that the unit's own condition took away. They cannot
buy back the arbitration that a disclosure took away, because the buyer was
told.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autozest.catalog.accounts import AccountClass
from autozest.inspection.announcements import AnnouncementCode, Light, forced_light
from autozest.money import Money

#: A unit at or below this grade runs red whatever else is true.
AS_IS_GRADE = Decimal("2.0")

#: A unit at or below this grade runs no better than yellow.
LIMITED_GRADE = Decimal("3.0")

#: Under this the claim costs more to handle than the unit is worth.
AS_IS_PRICE = Money.parse("3000.00")

#: Seller classes that never do better than this light.
_CLASS_CEILING: dict[AccountClass, Light] = {
    AccountClass.FINANCE: Light.YELLOW,
    AccountClass.PUBLIC: Light.RED,
}

#: Seller classes whose units start greener than their grade would suggest.
_CLASS_FLOOR: dict[AccountClass, Light] = {AccountClass.FACTORY: Light.GREEN}


@dataclass(frozen=True)
class LightDecision:
    """The light, and every rule that had a hand in it."""

    light: Light
    reasons: tuple[str, ...]
    relaxed: tuple[str, ...] = ()

    @property
    def was_forced(self) -> bool:
        return any(reason.startswith("announced") for reason in self.reasons)

    def explain(self) -> str:
        why = "; ".join(self.reasons) if self.reasons else "nothing against it"
        text = f"{self.light.value} because {why}"
        if self.relaxed:
            text += f" (guarantee relaxed {', '.join(self.relaxed)})"
        return text


def _more_restrictive(left: Light, right: Light) -> Light:
    return left if left.rank >= right.rank else right


def assign(
    *,
    announcements: set[AnnouncementCode],
    grade: Decimal | None = None,
    floor: Money | None = None,
    seller_class: AccountClass = AccountClass.INDEPENDENT_DEALER,
    guaranteed: bool = False,
) -> LightDecision:
    """Work out the light for one unit.

    ``guaranteed`` is the seller taking the condition risk back. It relaxes the
    rules that are about the unit and leaves the rule that is about disclosure
    exactly where it was.
    """
    reasons: list[str] = []
    relaxed: list[str] = []
    light = Light.GREEN

    announced = forced_light(announcements)
    if announced is not None:
        light = _more_restrictive(light, announced)
        reasons.append(f"announced {announced.value}")

    if grade is not None:
        if grade <= AS_IS_GRADE:
            if guaranteed:
                relaxed.append("the grade")
            else:
                light = _more_restrictive(light, Light.RED)
                reasons.append(f"grade {grade}")
        elif grade <= LIMITED_GRADE:
            if guaranteed:
                relaxed.append("the grade")
            else:
                light = _more_restrictive(light, Light.YELLOW)
                reasons.append(f"grade {grade}")

    if floor is not None and floor < AS_IS_PRICE:
        if guaranteed:
            relaxed.append("the price band")
        else:
            light = _more_restrictive(light, Light.RED)
            reasons.append(f"floor under {AS_IS_PRICE}")

    ceiling = _CLASS_CEILING.get(seller_class)
    if ceiling is not None:
        light = _more_restrictive(light, ceiling)
        reasons.append(f"{seller_class.value} seller")

    seller_floor = _CLASS_FLOOR.get(seller_class)
    if seller_floor is not None and announced is None and light.rank > seller_floor.rank:
        light = seller_floor
        reasons.append(f"{seller_class.value} seller")

    return LightDecision(light=light, reasons=tuple(reasons), relaxed=tuple(relaxed))


def would_be_green(
    announcements: set[AnnouncementCode], grade: Decimal | None, floor: Money | None
) -> bool:
    """Whether the unit clears everything, for a seller deciding on a guarantee."""
    return assign(announcements=announcements, grade=grade, floor=floor).light is Light.GREEN
