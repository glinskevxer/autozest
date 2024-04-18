"""Title brands, and the one rule that overrides every window in the system.

A brand is a permanent mark a state puts on a title. Once a unit has been
declared salvage it is salvage for the rest of its life, whatever is done to
it, and the buyer is entitled to know before they bid.

**An undisclosed brand unwinds the sale, whenever it is discovered.** Not
within seven days, not subject to the light, not above a price threshold. The
arbitration windows exist because a buyer could reasonably have found the
problem sooner. Nobody finds a Michigan salvage brand by driving the unit
around, and a title that arrives in the post six weeks later carrying a brand
nobody called is not a late claim. It is a sale of something other than what
was sold.

That is the reason this module knows about announcements at all. Every brand
maps to the call that would have disclosed it, and the gap between what the
title says and what was called is checked directly rather than going anywhere
near the arbitration policy.
"""

from __future__ import annotations

from enum import Enum

from autozest.inspection.announcements import AnnouncementCode


class TitleBrand(Enum):
    """What a state has marked on the title."""

    CLEAN = "clean"
    SALVAGE = "salvage"
    REBUILT = "rebuilt"
    FLOOD = "flood"
    LEMON = "lemon"
    JUNK = "junk"
    BONDED = "bonded"
    ODOMETER_DISCREPANCY = "odometer_discrepancy"
    NOT_ACTUAL_MILEAGE = "not_actual_mileage"

    @property
    def is_clean(self) -> bool:
        return self is TitleBrand.CLEAN

    @property
    def is_terminal(self) -> bool:
        """Brands that mean the unit can never be titled for the road again."""
        return self in (TitleBrand.JUNK,)

    @property
    def blocks_retail(self) -> bool:
        """Brands a franchise dealer cannot retail the unit under."""
        return self not in (TitleBrand.CLEAN, TitleBrand.BONDED)


#: The call that discloses each brand. A brand with no entry needs no call.
DISCLOSURE: dict[TitleBrand, AnnouncementCode] = {
    TitleBrand.SALVAGE: AnnouncementCode.SALVAGE_TITLE,
    TitleBrand.REBUILT: AnnouncementCode.REBUILT_TITLE,
    TitleBrand.FLOOD: AnnouncementCode.FLOOD_TITLE,
    TitleBrand.LEMON: AnnouncementCode.LEMON_BUYBACK,
    TitleBrand.JUNK: AnnouncementCode.JUNK_TITLE,
    TitleBrand.BONDED: AnnouncementCode.BONDED_TITLE,
    TitleBrand.ODOMETER_DISCREPANCY: AnnouncementCode.TRUE_MILEAGE_UNKNOWN,
    TitleBrand.NOT_ACTUAL_MILEAGE: AnnouncementCode.TRUE_MILEAGE_UNKNOWN,
}

#: Calls that cover a brand even though they are not its own disclosure.
_ALSO_COVERS: dict[TitleBrand, frozenset[AnnouncementCode]] = {
    TitleBrand.REBUILT: frozenset({AnnouncementCode.SALVAGE_TITLE}),
    TitleBrand.SALVAGE: frozenset({AnnouncementCode.JUNK_TITLE}),
    TitleBrand.ODOMETER_DISCREPANCY: frozenset({AnnouncementCode.ODOMETER_EXEMPT}),
    TitleBrand.NOT_ACTUAL_MILEAGE: frozenset({AnnouncementCode.ODOMETER_EXEMPT}),
}


def required_call(brand: TitleBrand) -> AnnouncementCode | None:
    """The announcement that discloses this brand, if one is needed."""
    return DISCLOSURE.get(brand)


def was_disclosed(brand: TitleBrand, announcements: set[AnnouncementCode]) -> bool:
    """Whether the block called something that covers this brand."""
    wanted = required_call(brand)
    if wanted is None:
        return True
    if wanted in announcements:
        return True
    if AnnouncementCode.AS_IS in announcements and brand is not TitleBrand.JUNK:
        # As-is covers condition, and a state brand is not a condition. Junk is
        # kept out of even that, because a unit that cannot be titled again is
        # not something an as-is call can quietly include.
        return False
    return bool(_ALSO_COVERS.get(brand, frozenset()) & announcements)


def undisclosed_brand(
    brand: TitleBrand, announcements: set[AnnouncementCode]
) -> TitleBrand | None:
    """The brand that should have been called and was not, or ``None``."""
    if brand.is_clean:
        return None
    return None if was_disclosed(brand, announcements) else brand


def brands_called(announcements: set[AnnouncementCode]) -> set[TitleBrand]:
    """Which brands the block's calls would have covered."""
    return {
        brand
        for brand in TitleBrand
        if not brand.is_clean and was_disclosed(brand, announcements)
    }
