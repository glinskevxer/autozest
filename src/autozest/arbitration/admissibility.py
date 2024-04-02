"""Whether a claim is heard at all.

This is where the whole system comes together, and it is worth saying plainly
what the shape of it is: the inspection floor decided the announcements, the
announcements decided the light, and the light decides which of the buyer's
complaints are even listened to. Nobody typed that chain in. It falls out of a
dented rail and a floor price.

The checks run in a fixed order and the order is not decoration. The first one
that fails is the one reported, because that is the one an operator has to
explain to a buyer on the phone, and telling them the window expired when the
real answer is that the defect was announced sends them away angry and back
again tomorrow.

1. **Announced.** The defect was called at the block. Nothing else matters.
2. **Light.** The light this unit ran under does not hear this class.
3. **Arbitrable.** Nobody hears this class, under any light.
4. **Threshold.** The hammer is under the floor for this class.
5. **Window.** The clock ran out.
6. **Repeat.** This class has already been claimed and decided on this unit.

Two things cut across that order.

A **mechanical guarantee** the seller called at the block beats both the
announcement bar and the light gate, for the classes it names and no others. A
seller who guarantees the drivetrain on a yellow unit has bought the buyer a
green unit for drivetrain purposes only.

A **post sale inspection** stops the clock on the mechanical classes from the
moment it is ordered until it is finished. A buyer who orders one on the night
is not punished for the shop being busy on Wednesday.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from autozest.arbitration.policy import ArbitrationPolicy
from autozest.defects import DefectClass
from autozest.inspection.announcements import AnnouncementCode, Light, bars_class, grants_class
from autozest.money import Money
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar, Window


@dataclass(frozen=True)
class ClaimContext:
    """Everything the decision looks at, gathered in one place."""

    defect: DefectClass
    light: Light
    announcements: set[AnnouncementCode]
    hammer: Money
    sold_at: datetime
    claimed_at: datetime
    inspection_ordered_at: datetime | None = None
    inspection_completed_at: datetime | None = None
    settled_classes: frozenset[DefectClass] = frozenset()
    calendar: BusinessCalendar = field(default_factory=lambda: DEFAULT_CALENDAR)


@dataclass(frozen=True)
class Ruling:
    """Whether the claim is heard, and the one reason if it is not."""

    admissible: bool
    rule: str | None = None
    detail: str | None = None
    deadline: datetime | None = None
    window: Window | None = None
    granted_by_guarantee: bool = False

    def explain(self) -> str:
        if self.admissible:
            granted = " on the seller's guarantee" if self.granted_by_guarantee else ""
            return f"heard{granted}, the window closes {self.deadline}"
        return f"{self.rule}: {self.detail}"


def deadline_for(context: ClaimContext, window: Window) -> datetime:
    """When the clock runs out, allowing for a post sale inspection.

    An inspection stops the clock for the mechanical classes only. It has
    nothing to say about a title or an odometer, and letting it pause those
    would hand a buyer three extra months for the price of a shop visit.
    """
    base = context.calendar.deadline(context.sold_at, window)
    if not context.defect.is_mechanical or context.inspection_ordered_at is None:
        return base
    finished = context.inspection_completed_at or context.claimed_at
    paused = finished - context.inspection_ordered_at
    if paused.total_seconds() <= 0:
        return base
    return context.calendar.deadline(context.sold_at + paused, window)


def assess(context: ClaimContext, policy: ArbitrationPolicy | None = None) -> Ruling:
    """Decide whether the claim is heard."""
    rules = policy or ArbitrationPolicy()
    rule = rules.rule_for(context.defect)
    guaranteed = grants_class(context.announcements, context.defect)

    if not guaranteed and bars_class(context.announcements, context.defect):
        return Ruling(
            admissible=False,
            rule="arb.announced",
            detail=f"{context.defect.value} was announced at the block",
        )

    if not guaranteed and context.defect not in _heard_by(context.light):
        return Ruling(
            admissible=False,
            rule="arb.light",
            detail=f"a {context.light.value} unit is not heard on {context.defect.value}",
        )

    if not rule.is_arbitrable:
        return Ruling(
            admissible=False,
            rule="arb.not_arbitrable",
            detail=f"{context.defect.value} is not an arbitrable class",
        )

    if context.hammer < rule.threshold:
        return Ruling(
            admissible=False,
            rule="arb.threshold",
            detail=f"{context.defect.value} needs a hammer of {rule.threshold} or more",
        )

    if context.defect in context.settled_classes:
        return Ruling(
            admissible=False,
            rule="arb.repeat",
            detail=f"{context.defect.value} has already been decided on this unit",
        )

    closes = deadline_for(context, rule.window)
    if context.claimed_at > closes:
        return Ruling(
            admissible=False,
            rule="arb.window",
            detail=f"the {rule.window} window closed at {closes}",
            deadline=closes,
            window=rule.window,
        )

    return Ruling(
        admissible=True,
        deadline=closes,
        window=rule.window,
        granted_by_guarantee=guaranteed,
    )


def _heard_by(light: Light) -> frozenset[DefectClass]:
    from autozest.arbitration.policy import classes_for

    return classes_for(light)


def admissible_classes(
    context: ClaimContext, policy: ArbitrationPolicy | None = None
) -> set[DefectClass]:
    """Every class this unit could still be claimed on right now.

    Used by the desk when a buyer rings up describing something vaguely. It is
    faster to tell them what is open than to have them guess.
    """
    from dataclasses import replace

    open_classes: set[DefectClass] = set()
    for defect in DefectClass:
        if assess(replace(context, defect=defect), policy).admissible:
            open_classes.add(defect)
    return open_classes
