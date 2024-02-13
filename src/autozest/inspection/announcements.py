"""What gets called at the block, and what calling it costs the buyer.

An announcement is a disclosure made before the bidding starts. The rule behind
every one of them is the same and it is the hinge the whole arbitration desk
turns on: **a defect that was announced cannot be arbitrated**. A buyer who
heard "frame damage" and bid anyway bought a unit with frame damage.

So the announcement set is not decoration on the block screen. It decides which
of the buyer's later complaints are admissible at all, which means the
inspection floor is quietly writing the arbitration policy for that unit.

Some announcements also force the light. A unit called as-is runs red whatever
its grade, and a red unit has almost no arbitration left. That is a second,
longer path from the same finding to the same place.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from autozest.defects import DefectClass
from autozest.errors import ValidationError


class AnnouncementSource(Enum):
    """Where the announcement came from. Mirrors the check on the table."""

    INSPECTION = "inspection"
    TITLE = "title"
    ODOMETER = "odometer"
    SELLER = "seller"
    MANUAL = "manual"


class Light(Enum):
    """The lane light a unit runs under.

    Green is a full arbitration. Yellow is arbitration on the announced terms
    and nothing else. Red is as-is, and blue is the title attorney lane where
    the unit sells but the paperwork is still being chased.
    """

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    BLUE = "blue"

    @property
    def rank(self) -> int:
        """How restrictive the light is. Higher is more restrictive."""
        return _LIGHT_RANK[self]


_LIGHT_RANK = {Light.GREEN: 0, Light.YELLOW: 1, Light.BLUE: 2, Light.RED: 3}


class AnnouncementCode(Enum):
    """Every disclosure this auction knows how to call."""

    AS_IS = "as_is"
    TITLE_ABSENT = "title_absent"
    TITLE_ATTORNEY = "title_attorney"
    SALVAGE_TITLE = "salvage_title"
    REBUILT_TITLE = "rebuilt_title"
    FLOOD_TITLE = "flood_title"
    LEMON_BUYBACK = "lemon_buyback"
    JUNK_TITLE = "junk_title"
    BONDED_TITLE = "bonded_title"
    TRUE_MILEAGE_UNKNOWN = "true_mileage_unknown"
    ODOMETER_EXEMPT = "odometer_exempt"
    ODOMETER_OVER_LIMIT = "odometer_over_limit"
    FRAME_DAMAGE = "frame_damage"
    UNIBODY_DAMAGE = "unibody_damage"
    STRUCTURAL_ALTERATION = "structural_alteration"
    AIRBAG_DEPLOYED = "airbag_deployed"
    AIRBAG_MISSING = "airbag_missing"
    FLOOD_DAMAGE = "flood_damage"
    ENGINE_DEFECT = "engine_defect"
    TRANSMISSION_DEFECT = "transmission_defect"
    DRIVE_AXLE_DEFECT = "drive_axle_defect"
    EMISSIONS_NOT_CERTIFIED = "emissions_not_certified"
    CANADIAN_UNIT = "canadian_unit"
    EXPORT_ONLY = "export_only"
    PRIOR_TAXI = "prior_taxi"
    PRIOR_POLICE = "prior_police"
    PRIOR_RENTAL = "prior_rental"
    NO_KEYS = "no_keys"
    NOT_DRIVABLE = "not_drivable"
    MECHANICALLY_GUARANTEED = "mechanically_guaranteed"


@dataclass(frozen=True)
class AnnouncementSpec:
    """What one announcement means to the rest of the system."""

    code: AnnouncementCode
    label: str
    bars: frozenset[DefectClass]
    forces: Light | None = None
    grants: frozenset[DefectClass] = frozenset()

    @property
    def is_title_matter(self) -> bool:
        return DefectClass.TITLE in self.bars or DefectClass.BRANDING in self.bars


def _spec(
    code: AnnouncementCode,
    label: str,
    bars: set[DefectClass] | None = None,
    forces: Light | None = None,
    grants: set[DefectClass] | None = None,
) -> AnnouncementSpec:
    return AnnouncementSpec(
        code=code,
        label=label,
        bars=frozenset(bars or set()),
        forces=forces,
        grants=frozenset(grants or set()),
    )


_ALL_CLASSES = frozenset(DefectClass)

REGISTRY: dict[AnnouncementCode, AnnouncementSpec] = {
    spec.code: spec
    for spec in (
        _spec(AnnouncementCode.AS_IS, "as is, no arbitration", set(_ALL_CLASSES), Light.RED),
        _spec(
            AnnouncementCode.TITLE_ABSENT,
            "title not present",
            {DefectClass.TITLE},
            Light.BLUE,
        ),
        _spec(
            AnnouncementCode.TITLE_ATTORNEY,
            "title attorney, paperwork being chased",
            {DefectClass.TITLE},
            Light.BLUE,
        ),
        _spec(
            AnnouncementCode.SALVAGE_TITLE,
            "salvage title",
            {DefectClass.BRANDING, DefectClass.STRUCTURAL},
            Light.RED,
        ),
        _spec(
            AnnouncementCode.REBUILT_TITLE,
            "rebuilt title",
            {DefectClass.BRANDING, DefectClass.STRUCTURAL},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.FLOOD_TITLE,
            "flood title",
            {DefectClass.BRANDING, DefectClass.FLOOD, DefectClass.ELECTRICAL},
            Light.RED,
        ),
        _spec(
            AnnouncementCode.LEMON_BUYBACK,
            "manufacturer buyback",
            {DefectClass.BRANDING},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.JUNK_TITLE,
            "junk title, parts only",
            set(_ALL_CLASSES),
            Light.RED,
        ),
        _spec(
            AnnouncementCode.BONDED_TITLE,
            "bonded title",
            {DefectClass.TITLE, DefectClass.BRANDING},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.TRUE_MILEAGE_UNKNOWN,
            "true mileage unknown",
            {DefectClass.ODOMETER},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.ODOMETER_EXEMPT,
            "odometer exempt",
            {DefectClass.ODOMETER},
        ),
        _spec(
            AnnouncementCode.ODOMETER_OVER_LIMIT,
            "odometer past its mechanical limit",
            {DefectClass.ODOMETER},
        ),
        _spec(
            AnnouncementCode.FRAME_DAMAGE,
            "frame damage",
            {DefectClass.STRUCTURAL},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.UNIBODY_DAMAGE,
            "unibody damage",
            {DefectClass.STRUCTURAL},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.STRUCTURAL_ALTERATION,
            "structural alteration",
            {DefectClass.STRUCTURAL},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.AIRBAG_DEPLOYED,
            "airbag deployed",
            {DefectClass.AIRBAG},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.AIRBAG_MISSING,
            "airbag missing",
            {DefectClass.AIRBAG, DefectClass.MISSING_EQUIPMENT},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.FLOOD_DAMAGE,
            "flood damage",
            {DefectClass.FLOOD, DefectClass.ELECTRICAL},
            Light.RED,
        ),
        _spec(
            AnnouncementCode.ENGINE_DEFECT,
            "engine defect",
            {DefectClass.ENGINE},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.TRANSMISSION_DEFECT,
            "transmission defect",
            {DefectClass.TRANSMISSION},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.DRIVE_AXLE_DEFECT,
            "drive axle defect",
            {DefectClass.DRIVE_AXLE},
            Light.YELLOW,
        ),
        _spec(
            AnnouncementCode.EMISSIONS_NOT_CERTIFIED,
            "not certified for all states",
            {DefectClass.EMISSIONS},
        ),
        _spec(AnnouncementCode.CANADIAN_UNIT, "canadian unit", {DefectClass.ODOMETER}),
        _spec(AnnouncementCode.EXPORT_ONLY, "export only", {DefectClass.TITLE}, Light.RED),
        _spec(AnnouncementCode.PRIOR_TAXI, "prior taxi use", {DefectClass.PRIOR_USE}),
        _spec(AnnouncementCode.PRIOR_POLICE, "prior police use", {DefectClass.PRIOR_USE}),
        _spec(AnnouncementCode.PRIOR_RENTAL, "prior rental use", {DefectClass.PRIOR_USE}),
        _spec(AnnouncementCode.NO_KEYS, "no keys", {DefectClass.MISSING_EQUIPMENT}),
        _spec(
            AnnouncementCode.NOT_DRIVABLE,
            "will not move under its own power",
            {DefectClass.ENGINE, DefectClass.TRANSMISSION, DefectClass.DRIVE_AXLE},
            Light.RED,
        ),
        _spec(
            AnnouncementCode.MECHANICALLY_GUARANTEED,
            "mechanically guaranteed",
            set(),
            None,
            {DefectClass.ENGINE, DefectClass.TRANSMISSION, DefectClass.DRIVE_AXLE},
        ),
    )
}


def spec_for(code: AnnouncementCode) -> AnnouncementSpec:
    try:
        return REGISTRY[code]
    except KeyError as exc:  # pragma: no cover - the registry covers the enum
        raise ValidationError(f"no announcement registered for {code}") from exc


def bars_class(codes: set[AnnouncementCode], defect: DefectClass) -> bool:
    """Whether anything called at the block covers this class of defect."""
    return any(defect in spec_for(code).bars for code in codes)


def grants_class(codes: set[AnnouncementCode], defect: DefectClass) -> bool:
    """Whether a guarantee was called that opens this class up."""
    return any(defect in spec_for(code).grants for code in codes)


def forced_light(codes: set[AnnouncementCode]) -> Light | None:
    """The most restrictive light any of these announcements forces."""
    forced = [spec_for(code).forces for code in codes if spec_for(code).forces is not None]
    if not forced:
        return None
    return max((light for light in forced if light is not None), key=lambda light: light.rank)


def labels(codes: set[AnnouncementCode]) -> list[str]:
    """What the block announcer reads out, in a stable order."""
    return [spec_for(code).label for code in sorted(codes, key=lambda c: c.value)]
