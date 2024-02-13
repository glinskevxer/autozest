"""The defect classes, shared by the inspection floor and the arbitration desk.

This is deliberately one list. An inspector finding a bent rail and a buyer
claiming a bent rail six days later have to be talking about the same thing, and
the only way to be sure of that is for both of them to name the same class.

The class is what carries policy. A window, a price threshold and whether an
announcement bars a claim are all looked up by class, never by the words a
buyer typed into a claim form.
"""

from __future__ import annotations

from enum import Enum


class DefectClass(Enum):
    """What kind of problem this is."""

    STRUCTURAL = "structural"
    ODOMETER = "odometer"
    TITLE = "title"
    BRANDING = "branding"
    ENGINE = "engine"
    TRANSMISSION = "transmission"
    DRIVE_AXLE = "drive_axle"
    EMISSIONS = "emissions"
    AIRBAG = "airbag"
    FLOOD = "flood"
    ELECTRICAL = "electrical"
    SUSPENSION = "suspension"
    BODY_COSMETIC = "body_cosmetic"
    MISSING_EQUIPMENT = "missing_equipment"
    PRIOR_USE = "prior_use"

    @property
    def is_mechanical(self) -> bool:
        """Classes a drivetrain inspection would find."""
        return self in _MECHANICAL

    @property
    def is_paperwork(self) -> bool:
        """Classes that live on the title rather than on the unit."""
        return self in _PAPERWORK

    @property
    def survives_inspection(self) -> bool:
        """Classes a buyer could not have seen on the lot before bidding.

        This is the reason the arbitration windows are not all the same. A
        dented door was there to be looked at. A rolled odometer was not.
        """
        return self in _NOT_VISIBLE


_MECHANICAL = frozenset(
    {
        DefectClass.ENGINE,
        DefectClass.TRANSMISSION,
        DefectClass.DRIVE_AXLE,
        DefectClass.ELECTRICAL,
        DefectClass.SUSPENSION,
    }
)

_PAPERWORK = frozenset(
    {DefectClass.TITLE, DefectClass.BRANDING, DefectClass.ODOMETER, DefectClass.PRIOR_USE}
)

_NOT_VISIBLE = frozenset(
    {
        DefectClass.STRUCTURAL,
        DefectClass.ODOMETER,
        DefectClass.TITLE,
        DefectClass.BRANDING,
        DefectClass.FLOOD,
        DefectClass.PRIOR_USE,
        DefectClass.AIRBAG,
    }
)


def mechanical_classes() -> frozenset[DefectClass]:
    return _MECHANICAL


def paperwork_classes() -> frozenset[DefectClass]:
    return _PAPERWORK
