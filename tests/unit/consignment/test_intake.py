import sqlite3
from datetime import date

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository, AccountStatus
from autozest.catalog.odometer import OdometerStatus
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import ConsignmentStatus
from autozest.errors import NotFound, PolicyViolation, StateError, ValidationError
from autozest.money import Money
from autozest.vin import Vin

ACCORD = "1HGCM82633A004352"
MODEL3 = "5YJ3E1EA0KF000316"
COROLLA = "JTDKN3DU8A0000001"


@pytest.fixture()
def intake(db: sqlite3.Connection) -> IntakeService:
    accounts = AccountRepository(db)
    accounts.add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-44192",
            state="TX",
        )
    )
    accounts.add(Account(id="p-1", name="Walk In", account_class=AccountClass.PUBLIC))
    accounts.add(
        Account(
            id="d-200",
            name="Suspended Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-99999",
            state="TX",
            status=AccountStatus.SUSPENDED,
        )
    )
    vehicles = VehicleRepository(db)
    vehicles.add(Vehicle(vin=Vin.parse(ACCORD), make="Honda", model="Accord", model_year=2019))
    vehicles.add(Vehicle(vin=Vin.parse(MODEL3), make="Tesla", model="Model 3"))
    return IntakeService(db)


def test_registering_a_unit(intake: IntakeService) -> None:
    unit = intake.register(ACCORD, "d-100", floor=Money.parse("4500.00"))
    assert unit.id == "C-2024-000001" or unit.id.startswith("C-")
    assert unit.status is ConsignmentStatus.REGISTERED
    assert unit.floor == Money.parse("4500.00")
    assert unit.floor_set_at is not None


def test_a_public_account_is_turned_away_at_registration(intake: IntakeService) -> None:
    with pytest.raises(PolicyViolation) as caught:
        intake.register(ACCORD, "p-1")
    assert caught.value.rule == "account.consign"


def test_a_suspended_seller_is_turned_away(intake: IntakeService) -> None:
    with pytest.raises(StateError):
        intake.register(ACCORD, "d-200")


def test_a_unit_not_on_file_cannot_be_registered(intake: IntakeService) -> None:
    with pytest.raises(NotFound) as caught:
        intake.register(COROLLA, "d-100")
    assert caught.value.kind == "vehicle"


def test_checking_in_records_the_gate_reading_against_the_vin(
    intake: IntakeService,
) -> None:
    unit = intake.register(ACCORD, "d-100")
    arrived = intake.check_in(unit.id, Arrival(keys_count=2, miles=41_930))
    assert arrived.status is ConsignmentStatus.ARRIVED
    assert arrived.keys_count == 2
    assert arrived.gate_pass is not None
    history = intake.vehicles.history(ACCORD)
    assert [r.miles for r in history.readings] == [41_930]


def test_the_gate_reading_is_what_the_odometer_status_is_derived_from(
    intake: IntakeService,
) -> None:
    unit = intake.register(ACCORD, "d-100")
    intake.check_in(unit.id, Arrival(keys_count=1, miles=41_930))
    history = intake.vehicles.history(ACCORD)
    assert history.status(2019, date(2024, 3, 12)) is OdometerStatus.ACTUAL


def test_a_second_check_in_is_refused(intake: IntakeService) -> None:
    unit = intake.register(ACCORD, "d-100")
    intake.check_in(unit.id, Arrival(keys_count=2, miles=41_930))
    with pytest.raises(PolicyViolation) as caught:
        intake.check_in(unit.id, Arrival(keys_count=2, miles=41_930))
    assert caught.value.rule == "intake.already_arrived"


def test_a_negative_key_count_never_gets_as_far_as_the_gate() -> None:
    with pytest.raises(ValidationError):
        Arrival(keys_count=-1, miles=100)


def test_no_keys_is_allowed_because_it_happens(intake: IntakeService) -> None:
    unit = intake.register(ACCORD, "d-100")
    assert intake.check_in(unit.id, Arrival(keys_count=0, miles=41_930)).keys_count == 0


def test_gate_passes_do_not_repeat(intake: IntakeService) -> None:
    first = intake.register(ACCORD, "d-100")
    second = intake.register(MODEL3, "d-100")
    one = intake.check_in(first.id, Arrival(keys_count=2, miles=41_930))
    two = intake.check_in(second.id, Arrival(keys_count=1, miles=12_100))
    assert one.gate_pass != two.gate_pass


def test_the_gate_can_look_a_unit_up_three_ways(intake: IntakeService) -> None:
    unit = intake.register(ACCORD, "d-100")
    arrived = intake.check_in(unit.id, Arrival(keys_count=2, miles=41_930))
    assert intake.find(unit.id).id == unit.id
    assert intake.find(ACCORD).id == unit.id
    assert arrived.gate_pass is not None
    assert intake.find(arrived.gate_pass.lower()).id == unit.id


def test_looking_up_something_that_was_never_here(intake: IntakeService) -> None:
    with pytest.raises(NotFound):
        intake.find("ZZZZZ")


def test_a_vin_that_has_been_here_twice_finds_the_live_one(intake: IntakeService) -> None:
    first = intake.register(ACCORD, "d-100")
    intake.withdraw(first.id, "seller changed their mind")
    second = intake.register(ACCORD, "d-100")
    assert intake.find(ACCORD).id == second.id


def test_withdrawing_needs_a_reason_and_keeps_it(intake: IntakeService) -> None:
    unit = intake.register(ACCORD, "d-100")
    with pytest.raises(ValidationError, match="needs a reason"):
        intake.withdraw(unit.id, "   ")
    withdrawn = intake.withdraw(unit.id, "sold retail before the sale")
    assert withdrawn.status is ConsignmentStatus.WITHDRAWN
    assert withdrawn.note == "sold retail before the sale"


def test_only_a_no_sale_can_be_released(intake: IntakeService) -> None:
    unit = intake.register(ACCORD, "d-100")
    with pytest.raises(PolicyViolation) as caught:
        intake.release(unit.id)
    assert caught.value.rule == "intake.release"


def test_a_no_sale_goes_home(intake: IntakeService) -> None:
    unit = intake.register(ACCORD, "d-100")
    unit = intake.check_in(unit.id, Arrival(keys_count=2, miles=41_930))
    for status in (
        ConsignmentStatus.INSPECTED,
        ConsignmentStatus.CATALOGUED,
        ConsignmentStatus.NO_SALE,
    ):
        unit = intake.consignments.save_status(unit, status)
    assert intake.release(unit.id).status is ConsignmentStatus.RELEASED


def test_the_expected_arrivals_list_empties_as_units_turn_up(
    intake: IntakeService,
) -> None:
    first = intake.register(ACCORD, "d-100")
    intake.register(MODEL3, "d-100")
    assert len(intake.expected_arrivals()) == 2
    intake.check_in(first.id, Arrival(keys_count=2, miles=41_930))
    assert len(intake.expected_arrivals()) == 1
    assert len(intake.on_the_ground()) == 1
