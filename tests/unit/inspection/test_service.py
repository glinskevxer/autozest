import sqlite3
from datetime import datetime
from decimal import Decimal

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.odometer import Reading
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import ConsignmentStatus
from autozest.errors import NotFound, PolicyViolation
from autozest.inspection.announcements import AnnouncementCode
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.vin import Vin

ACCORD = "1HGCM82633A004352"
MODEL3 = "5YJ3E1EA0KF000316"


@pytest.fixture()
def yard(db: sqlite3.Connection) -> tuple[IntakeService, InspectionService]:
    AccountRepository(db).add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-44192",
            state="TX",
        )
    )
    vehicles = VehicleRepository(db)
    vehicles.add(Vehicle(vin=Vin.parse(ACCORD), make="Honda", model="Accord", model_year=2019))
    vehicles.add(Vehicle(vin=Vin.parse(MODEL3), make="Tesla", model="Model 3"))
    return IntakeService(db), InspectionService(db)


def arrived(
    yard: tuple[IntakeService, InspectionService], vin: str = ACCORD, **kwargs: object
) -> str:
    intake, _ = yard
    unit = intake.register(vin, "d-100", **kwargs)  # type: ignore[arg-type]
    intake.check_in(unit.id, Arrival(keys_count=2, miles=41_930))
    return unit.id


def line(panel: Panel, kind: DamageKind, severity: int, cost: str | None = None) -> DamageLine:
    return DamageLine(
        panel=panel, kind=kind, severity=severity, estimate=Money.parse(cost) if cost else None
    )


def test_a_report_cannot_be_opened_before_the_unit_arrives(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    intake, inspection = yard
    unit = intake.register(ACCORD, "d-100")
    with pytest.raises(PolicyViolation) as caught:
        inspection.open_report(unit.id, "j.hale")
    assert caught.value.rule == "inspection.not_arrived"


def test_opening_a_report(yard: tuple[IntakeService, InspectionService]) -> None:
    _, inspection = yard
    report = inspection.open_report(arrived(yard), "j.hale")
    assert report.is_open
    assert report.inspector == "j.hale"
    assert report.grade is None


def test_report_references_run_in_sequence(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    _, inspection = yard
    first = inspection.open_report(arrived(yard, ACCORD), "j.hale")
    second = inspection.open_report(arrived(yard, MODEL3), "j.hale")
    assert first.id != second.id
    assert first.id.startswith("R-")


def test_damage_goes_on_and_comes_back(yard: tuple[IntakeService, InspectionService]) -> None:
    _, inspection = yard
    report = inspection.open_report(arrived(yard), "j.hale")
    inspection.add_damage(report.id, line(Panel.HOOD, DamageKind.DENT, 3))
    inspection.add_damage(report.id, line(Panel.WHEEL, DamageKind.CHIP, 1, "40.00"))
    found = inspection.damage(report.id)
    assert [d.panel for d in found] == [Panel.HOOD, Panel.WHEEL]
    assert found[1].cost == Money.parse("40.00")


def test_finalising_grades_the_unit_and_moves_the_consignment(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    intake, inspection = yard
    consignment_id = arrived(yard)
    report = inspection.open_report(consignment_id, "j.hale")
    inspection.add_damage(report.id, line(Panel.HOOD, DamageKind.DENT, 3, "1500.00"))
    signed, codes = inspection.finalise(report.id, reference_value=Money.parse("12000.00"))
    assert signed.grade == Decimal("4.0")
    assert not signed.is_open
    assert codes == set()
    assert intake.consignments.get(consignment_id).status is ConsignmentStatus.INSPECTED


def test_a_signed_report_takes_no_more_damage(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    _, inspection = yard
    report = inspection.open_report(arrived(yard), "j.hale")
    inspection.finalise(report.id)
    with pytest.raises(PolicyViolation) as caught:
        inspection.add_damage(report.id, line(Panel.HOOD, DamageKind.DENT, 3))
    assert caught.value.rule == "inspection.closed"


def test_a_report_is_signed_once(yard: tuple[IntakeService, InspectionService]) -> None:
    _, inspection = yard
    report = inspection.open_report(arrived(yard), "j.hale")
    inspection.finalise(report.id)
    with pytest.raises(PolicyViolation, match="already signed"):
        inspection.finalise(report.id)


def test_structural_damage_writes_an_announcement(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    _, inspection = yard
    consignment_id = arrived(yard)
    report = inspection.open_report(consignment_id, "j.hale")
    inspection.add_damage(report.id, line(Panel.RAIL, DamageKind.BENT, 2, "100.00"))
    _, codes = inspection.finalise(report.id, unibody=True)
    assert AnnouncementCode.UNIBODY_DAMAGE in codes
    assert inspection.announcements(consignment_id) == codes


def test_the_gate_reading_reaches_the_announcement_set(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    intake, inspection = yard
    unit = intake.register(ACCORD, "d-100")
    intake.check_in(unit.id, Arrival(keys_count=2, miles=88_400))
    intake.vehicles.record_reading(
        ACCORD, Reading(miles=31_100, read_on=datetime.now().date(), source="second look")
    )
    report = inspection.open_report(unit.id, "j.hale")
    _, codes = inspection.finalise(report.id)
    assert AnnouncementCode.TRUE_MILEAGE_UNKNOWN in codes


def test_a_unit_with_no_title_expected_is_called(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    _, inspection = yard
    consignment_id = arrived(yard, ACCORD, title_expected=False)
    report = inspection.open_report(consignment_id, "j.hale")
    _, codes = inspection.finalise(report.id)
    assert AnnouncementCode.TITLE_ABSENT in codes


def test_a_seller_declaration_is_recorded_as_coming_from_the_seller(
    db: sqlite3.Connection, yard: tuple[IntakeService, InspectionService]
) -> None:
    _, inspection = yard
    consignment_id = arrived(yard)
    report = inspection.open_report(consignment_id, "j.hale")
    inspection.finalise(report.id, declared=frozenset({AnnouncementCode.PRIOR_RENTAL}))
    row = db.execute(
        "SELECT source FROM announcement WHERE consignment_id = ? AND code = ?",
        (consignment_id, "prior_rental"),
    ).fetchone()
    assert row["source"] == "seller"


def test_the_latest_report_is_the_one_that_counts(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    _, inspection = yard
    consignment_id = arrived(yard)
    first = inspection.open_report(consignment_id, "j.hale", at=datetime(2024, 3, 10, 9, 0))
    inspection.finalise(first.id, at=datetime(2024, 3, 10, 10, 0))
    latest = inspection.latest_for(consignment_id)
    assert latest is not None and latest.id == first.id


def test_asking_for_a_report_that_was_never_written(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    _, inspection = yard
    consignment_id = arrived(yard)
    assert inspection.latest_for(consignment_id) is None
    with pytest.raises(NotFound):
        inspection.require_report(consignment_id)
    with pytest.raises(NotFound):
        inspection.get("R-2024-999999")


def test_calling_something_by_hand_after_the_report(
    yard: tuple[IntakeService, InspectionService],
) -> None:
    _, inspection = yard
    consignment_id = arrived(yard)
    report = inspection.open_report(consignment_id, "j.hale")
    inspection.finalise(report.id)
    inspection.add_announcement(consignment_id, AnnouncementCode.PRIOR_TAXI, "city fleet")
    assert AnnouncementCode.PRIOR_TAXI in inspection.announcements(consignment_id)
