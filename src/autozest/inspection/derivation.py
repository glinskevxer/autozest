"""Turning what the inspector found into what the announcer says.

Nobody types an announcement set by hand. It falls out of the findings, and it
has to, because the announcement set decides which arbitration claims are
admissible later and a set assembled from memory on a busy Tuesday is how a
unit ends up sold with a rolled odometer and no disclosure.

Two things here are worth reading twice.

The first is that the grade, which is itself computed from the damage, can add
an announcement of its own. A unit that grades into the salvage band is called
as-is, and as-is bars every class of claim there is. So a pile of small
cosmetic findings, none of which is announceable on its own, can add up to a
grade that removes the buyer's arbitration entirely.

The second is supersession. Some announcements swallow others. Calling both
salvage title and rebuilt title on the same unit is not more disclosure, it is
a contradiction, and the announcer only has room for one of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from autozest.catalog.odometer import OdometerStatus
from autozest.inspection import grade as grading
from autozest.inspection.announcements import AnnouncementCode, spec_for
from autozest.inspection.damage import DamageLine, structural_lines
from autozest.money import Money

#: A unit grading at or below this is called as is.
AS_IS_GRADE = Decimal("1.0")

#: Odometer statuses map straight onto a disclosure.
_ODOMETER_CODES = {
    OdometerStatus.NOT_ACTUAL: AnnouncementCode.TRUE_MILEAGE_UNKNOWN,
    OdometerStatus.EXEMPT: AnnouncementCode.ODOMETER_EXEMPT,
    OdometerStatus.EXCEEDS_MECHANICAL_LIMITS: AnnouncementCode.ODOMETER_OVER_LIMIT,
}

#: Left hand code swallows right hand codes when both are present.
SUPERSEDES: dict[AnnouncementCode, frozenset[AnnouncementCode]] = {
    AnnouncementCode.JUNK_TITLE: frozenset(
        {
            AnnouncementCode.SALVAGE_TITLE,
            AnnouncementCode.REBUILT_TITLE,
            AnnouncementCode.FLOOD_TITLE,
            AnnouncementCode.BONDED_TITLE,
        }
    ),
    AnnouncementCode.SALVAGE_TITLE: frozenset(
        {AnnouncementCode.REBUILT_TITLE, AnnouncementCode.BONDED_TITLE}
    ),
    AnnouncementCode.TITLE_ATTORNEY: frozenset({AnnouncementCode.TITLE_ABSENT}),
    AnnouncementCode.FLOOD_TITLE: frozenset({AnnouncementCode.FLOOD_DAMAGE}),
    AnnouncementCode.NOT_DRIVABLE: frozenset({AnnouncementCode.ENGINE_DEFECT}),
    AnnouncementCode.AS_IS: frozenset({AnnouncementCode.MECHANICALLY_GUARANTEED}),
}


@dataclass(frozen=True)
class Findings:
    """Everything the derivation looks at, in one place.

    Assembled by the inspection service from the condition report, the odometer
    history and whatever the seller declared on the consignment.
    """

    damage: list[DamageLine] = field(default_factory=list)
    drivable: bool = True
    engine_starts: bool = True
    tread_32nds: int | None = None
    keys_count: int = 2
    odometer_status: OdometerStatus = OdometerStatus.ACTUAL
    title_present: bool = True
    title_chased: bool = False
    unibody: bool = True
    reference_value: Money | None = None
    declared: frozenset[AnnouncementCode] = frozenset()

    @property
    def grade(self) -> grading.GradeResult:
        return grading.compute(
            self.damage,
            drivable=self.drivable,
            engine_starts=self.engine_starts,
            tread_32nds=self.tread_32nds,
            reference_value=self.reference_value,
        )


def derive(findings: Findings) -> set[AnnouncementCode]:
    """The announcement set for a unit, before supersession is applied."""
    codes: set[AnnouncementCode] = set(findings.declared)

    if structural_lines(findings.damage):
        codes.add(
            AnnouncementCode.UNIBODY_DAMAGE
            if findings.unibody
            else AnnouncementCode.FRAME_DAMAGE
        )

    if not findings.drivable:
        codes.add(AnnouncementCode.NOT_DRIVABLE)
    if not findings.engine_starts:
        codes.add(AnnouncementCode.ENGINE_DEFECT)
    if findings.keys_count == 0:
        codes.add(AnnouncementCode.NO_KEYS)

    odometer_code = _ODOMETER_CODES.get(findings.odometer_status)
    if odometer_code is not None:
        codes.add(odometer_code)

    if not findings.title_present:
        codes.add(
            AnnouncementCode.TITLE_ATTORNEY
            if findings.title_chased
            else AnnouncementCode.TITLE_ABSENT
        )

    # The grade is computed from the damage, and a low enough grade is itself
    # an announcement. Small findings that are individually silent can add up
    # to one that takes the buyer's arbitration away.
    if findings.grade.grade <= AS_IS_GRADE:
        codes.add(AnnouncementCode.AS_IS)

    return codes


def supersede(codes: set[AnnouncementCode]) -> set[AnnouncementCode]:
    """Drop announcements that a stronger one already covers."""
    swallowed: set[AnnouncementCode] = set()
    for code in codes:
        swallowed |= SUPERSEDES.get(code, frozenset()) & codes
    return codes - swallowed


def announce(findings: Findings) -> set[AnnouncementCode]:
    """The set an announcer actually reads out."""
    return supersede(derive(findings))


def redundant(codes: set[AnnouncementCode]) -> set[AnnouncementCode]:
    """Announcements in the set that add nothing the others do not already."""
    return codes - supersede(codes)


def contradictions(
    codes: set[AnnouncementCode],
) -> list[tuple[AnnouncementCode, AnnouncementCode]]:
    """Pairs that cannot both be true of the same unit."""
    found: list[tuple[AnnouncementCode, AnnouncementCode]] = []
    for left in sorted(codes, key=lambda c: c.value):
        granted = spec_for(left).grants
        if not granted:
            continue
        for right in sorted(codes, key=lambda c: c.value):
            if right is left:
                continue
            if granted & spec_for(right).bars:
                found.append((left, right))
    return found
