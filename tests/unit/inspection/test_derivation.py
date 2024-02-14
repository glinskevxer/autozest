from decimal import Decimal

from autozest.catalog.odometer import OdometerStatus
from autozest.inspection.announcements import AnnouncementCode, Light, forced_light
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.inspection.derivation import (
    Findings,
    announce,
    contradictions,
    derive,
    redundant,
    supersede,
)
from autozest.money import Money


def line(panel: Panel, kind: DamageKind, severity: int, cost: str | None = None) -> DamageLine:
    return DamageLine(
        panel=panel,
        kind=kind,
        severity=severity,
        estimate=Money.parse(cost) if cost else None,
    )


def test_a_clean_unit_gets_nothing_called() -> None:
    assert announce(Findings()) == set()


def test_structural_damage_on_a_unibody_is_called_unibody() -> None:
    findings = Findings(damage=[line(Panel.RAIL, DamageKind.BENT, 2, "100.00")], unibody=True)
    assert AnnouncementCode.UNIBODY_DAMAGE in announce(findings)
    assert AnnouncementCode.FRAME_DAMAGE not in announce(findings)


def test_the_same_damage_on_a_body_on_frame_truck_is_called_frame() -> None:
    findings = Findings(damage=[line(Panel.RAIL, DamageKind.BENT, 2, "100.00")], unibody=False)
    assert AnnouncementCode.FRAME_DAMAGE in announce(findings)


def test_cosmetic_damage_is_not_announced_at_all() -> None:
    findings = Findings(damage=[line(Panel.HOOD, DamageKind.DENT, 3, "200.00")])
    assert announce(findings) == set()


def test_no_keys_is_called() -> None:
    assert AnnouncementCode.NO_KEYS in announce(Findings(keys_count=0))
    assert AnnouncementCode.NO_KEYS not in announce(Findings(keys_count=1))


def test_the_odometer_status_maps_onto_a_disclosure() -> None:
    for status, code in (
        (OdometerStatus.NOT_ACTUAL, AnnouncementCode.TRUE_MILEAGE_UNKNOWN),
        (OdometerStatus.EXEMPT, AnnouncementCode.ODOMETER_EXEMPT),
        (OdometerStatus.EXCEEDS_MECHANICAL_LIMITS, AnnouncementCode.ODOMETER_OVER_LIMIT),
    ):
        assert code in announce(Findings(odometer_status=status))
    assert announce(Findings(odometer_status=OdometerStatus.ACTUAL)) == set()


def test_a_missing_title_is_called_absent_or_attorney() -> None:
    assert AnnouncementCode.TITLE_ABSENT in announce(Findings(title_present=False))
    chased = announce(Findings(title_present=False, title_chased=True))
    assert AnnouncementCode.TITLE_ATTORNEY in chased
    assert AnnouncementCode.TITLE_ABSENT not in chased


def test_a_unit_that_will_not_start_gets_two_calls_collapsed_into_one() -> None:
    findings = Findings(drivable=False, engine_starts=False)
    raw = derive(findings)
    assert AnnouncementCode.NOT_DRIVABLE in raw
    assert AnnouncementCode.ENGINE_DEFECT in raw
    assert AnnouncementCode.ENGINE_DEFECT not in announce(findings)


def test_small_cosmetic_findings_can_add_up_to_an_as_is_call() -> None:
    damage = [
        line(Panel.HOOD, DamageKind.DENT, 4),
        line(Panel.ROOF, DamageKind.HAIL, 5),
        line(Panel.LEFT_QUARTER, DamageKind.CREASE, 5),
        line(Panel.RIGHT_QUARTER, DamageKind.CREASE, 5),
        line(Panel.LEFT_FRONT_DOOR, DamageKind.CREASE, 5),
        line(Panel.RIGHT_FRONT_DOOR, DamageKind.CREASE, 5),
    ]
    findings = Findings(damage=damage, reference_value=Money.parse("9000.00"))
    assert findings.grade.grade <= Decimal("1.0")
    assert AnnouncementCode.AS_IS in announce(findings)


def test_that_as_is_call_takes_the_light_to_red() -> None:
    findings = Findings(drivable=False, engine_starts=False)
    assert forced_light(announce(findings)) is Light.RED


def test_a_seller_declaration_comes_through_untouched() -> None:
    findings = Findings(declared=frozenset({AnnouncementCode.PRIOR_RENTAL}))
    assert AnnouncementCode.PRIOR_RENTAL in announce(findings)


def test_junk_swallows_every_other_brand() -> None:
    codes = {
        AnnouncementCode.JUNK_TITLE,
        AnnouncementCode.SALVAGE_TITLE,
        AnnouncementCode.REBUILT_TITLE,
        AnnouncementCode.FLOOD_TITLE,
    }
    assert supersede(codes) == {AnnouncementCode.JUNK_TITLE}


def test_salvage_swallows_rebuilt_but_not_flood() -> None:
    codes = {
        AnnouncementCode.SALVAGE_TITLE,
        AnnouncementCode.REBUILT_TITLE,
        AnnouncementCode.FLOOD_TITLE,
    }
    assert supersede(codes) == {AnnouncementCode.SALVAGE_TITLE, AnnouncementCode.FLOOD_TITLE}


def test_the_attorney_lane_swallows_a_plain_absent_title() -> None:
    codes = {AnnouncementCode.TITLE_ATTORNEY, AnnouncementCode.TITLE_ABSENT}
    assert supersede(codes) == {AnnouncementCode.TITLE_ATTORNEY}


def test_supersession_leaves_unrelated_calls_alone() -> None:
    codes = {AnnouncementCode.NO_KEYS, AnnouncementCode.PRIOR_TAXI}
    assert supersede(codes) == codes


def test_redundant_names_what_was_dropped() -> None:
    codes = {AnnouncementCode.SALVAGE_TITLE, AnnouncementCode.REBUILT_TITLE}
    assert redundant(codes) == {AnnouncementCode.REBUILT_TITLE}
    assert redundant({AnnouncementCode.NO_KEYS}) == set()


def test_a_guarantee_beside_a_defect_call_is_a_contradiction() -> None:
    codes = {AnnouncementCode.MECHANICALLY_GUARANTEED, AnnouncementCode.ENGINE_DEFECT}
    assert contradictions(codes) == [
        (AnnouncementCode.MECHANICALLY_GUARANTEED, AnnouncementCode.ENGINE_DEFECT)
    ]


def test_a_guarantee_on_its_own_is_fine() -> None:
    assert contradictions({AnnouncementCode.MECHANICALLY_GUARANTEED}) == []
    assert contradictions({AnnouncementCode.FRAME_DAMAGE}) == []


def test_as_is_removes_a_guarantee_before_it_can_contradict_anything() -> None:
    codes = announce(
        Findings(
            drivable=False,
            engine_starts=False,
            declared=frozenset({AnnouncementCode.MECHANICALLY_GUARANTEED}),
        )
    )
    assert AnnouncementCode.MECHANICALLY_GUARANTEED not in codes
    assert contradictions(codes) == []
