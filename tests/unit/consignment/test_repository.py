import sqlite3
from datetime import date

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.models import Consignment, ConsignmentStatus
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import Conflict, NotFound, PolicyViolation, StateError
from autozest.money import Money
from autozest.vin import Vin

ACCORD = "1HGCM82633A004352"
MODEL3 = "5YJ3E1EA0KF000316"


@pytest.fixture()
def repo(db: sqlite3.Connection) -> ConsignmentRepository:
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
    vehicles = VehicleRepository(db)
    vehicles.add(Vehicle(vin=Vin.parse(ACCORD), make="Honda", model="Accord"))
    vehicles.add(Vehicle(vin=Vin.parse(MODEL3), make="Tesla", model="Model 3"))
    return ConsignmentRepository(db)


def unit(reference: str = "C-2024-000001", vin: str = ACCORD, **kwargs: object) -> Consignment:
    defaults: dict[str, object] = {"id": reference, "vin": vin, "seller_id": "d-100"}
    defaults.update(kwargs)
    return Consignment(**defaults)  # type: ignore[arg-type]


def test_the_first_reference_of_the_year(repo: ConsignmentRepository) -> None:
    assert repo.next_id(date(2024, 3, 12)) == "C-2024-000001"


def test_the_next_reference_follows_the_highest_already_used(
    repo: ConsignmentRepository,
) -> None:
    repo.add(unit("C-2024-000412"))
    assert repo.next_id(date(2024, 3, 12)) == "C-2024-000413"


def test_the_sequence_restarts_each_year(repo: ConsignmentRepository) -> None:
    repo.add(unit("C-2024-000412"))
    assert repo.next_id(date(2025, 1, 2)) == "C-2025-000001"


def test_round_trip(repo: ConsignmentRepository) -> None:
    repo.add(unit(floor=Money.parse("4500.00"), keys_count=2, transport_ref="TRN-88"))
    found = repo.get("C-2024-000001")
    assert found.vin == ACCORD
    assert found.floor == Money.parse("4500.00")
    assert found.keys_count == 2
    assert found.transport_ref == "TRN-88"
    assert found.title_expected


def test_an_unknown_reference(repo: ConsignmentRepository) -> None:
    with pytest.raises(NotFound):
        repo.get("C-2024-999999")


def test_a_vin_cannot_be_consigned_twice_at_once(repo: ConsignmentRepository) -> None:
    repo.add(unit("C-2024-000001"))
    with pytest.raises(Conflict) as caught:
        repo.add(unit("C-2024-000002"))
    assert caught.value.context["consignment"] == "C-2024-000001"


def test_a_vin_comes_back_once_the_first_consignment_closed(
    repo: ConsignmentRepository,
) -> None:
    first = repo.add(unit("C-2024-000001"))
    for status in (
        ConsignmentStatus.ARRIVED,
        ConsignmentStatus.INSPECTED,
        ConsignmentStatus.CATALOGUED,
        ConsignmentStatus.SOLD,
    ):
        first = repo.save_status(first, status)
    repo.add(unit("C-2024-000002"))
    assert len(repo.history_for_vin(ACCORD)) == 2


def test_the_open_consignment_is_the_live_one(repo: ConsignmentRepository) -> None:
    assert repo.find_open_for_vin(ACCORD) is None
    repo.add(unit())
    open_one = repo.find_open_for_vin(ACCORD)
    assert open_one is not None and open_one.id == "C-2024-000001"


def test_closing_stamps_the_closed_at(repo: ConsignmentRepository) -> None:
    added = repo.add(unit())
    withdrawn = repo.save_status(added, ConsignmentStatus.WITHDRAWN)
    assert withdrawn.closed_at is not None
    assert not withdrawn.is_open


def test_a_move_the_state_machine_forbids_never_reaches_the_database(
    repo: ConsignmentRepository,
) -> None:
    added = repo.add(unit())
    with pytest.raises(StateError):
        repo.save_status(added, ConsignmentStatus.SOLD)
    assert repo.get(added.id).status is ConsignmentStatus.REGISTERED


def test_listing_for_a_seller_and_narrowing_by_status(repo: ConsignmentRepository) -> None:
    first = repo.add(unit("C-2024-000001"))
    repo.add(unit("C-2024-000002", vin=MODEL3))
    repo.save_status(first, ConsignmentStatus.ARRIVED)
    assert len(repo.for_seller("d-100")) == 2
    assert len(repo.for_seller("d-100", ConsignmentStatus.ARRIVED)) == 1
    assert repo.for_seller("nobody") == []


def test_listing_by_status(repo: ConsignmentRepository) -> None:
    repo.add(unit("C-2024-000001"))
    repo.add(unit("C-2024-000002", vin=MODEL3))
    assert len(repo.with_status(ConsignmentStatus.REGISTERED)) == 2
    assert repo.with_status(ConsignmentStatus.SOLD) == []


def test_setting_a_floor(repo: ConsignmentRepository) -> None:
    repo.add(unit())
    updated = repo.set_floor("C-2024-000001", Money.parse("4500.00"))
    assert updated.floor == Money.parse("4500.00")
    assert updated.floor_set_at is not None


def test_a_floor_can_always_come_down(repo: ConsignmentRepository) -> None:
    added = repo.add(unit(floor=Money.parse("6000.00")))
    for status in (
        ConsignmentStatus.ARRIVED,
        ConsignmentStatus.INSPECTED,
        ConsignmentStatus.CATALOGUED,
    ):
        added = repo.save_status(added, status)
    lowered = repo.set_floor(added.id, Money.parse("5200.00"))
    assert lowered.floor == Money.parse("5200.00")


def test_a_floor_cannot_go_up_once_the_unit_is_catalogued(
    repo: ConsignmentRepository,
) -> None:
    added = repo.add(unit(floor=Money.parse("4500.00")))
    for status in (
        ConsignmentStatus.ARRIVED,
        ConsignmentStatus.INSPECTED,
        ConsignmentStatus.CATALOGUED,
    ):
        added = repo.save_status(added, status)
    with pytest.raises(PolicyViolation) as caught:
        repo.set_floor(added.id, Money.parse("6000.00"))
    assert caught.value.rule == "floor.raise_after_catalogue"


def test_a_floor_can_go_up_while_the_unit_is_still_being_worked(
    repo: ConsignmentRepository,
) -> None:
    added = repo.add(unit(floor=Money.parse("4500.00")))
    added = repo.save_status(added, ConsignmentStatus.ARRIVED)
    assert repo.set_floor(added.id, Money.parse("6000.00")).floor == Money.parse("6000.00")


def test_a_closed_consignment_takes_no_floor(repo: ConsignmentRepository) -> None:
    added = repo.add(unit())
    repo.save_status(added, ConsignmentStatus.WITHDRAWN)
    with pytest.raises(PolicyViolation) as caught:
        repo.set_floor(added.id, Money.parse("4500.00"))
    assert caught.value.rule == "floor.closed"


def test_a_negative_floor_is_refused(repo: ConsignmentRepository) -> None:
    repo.add(unit())
    with pytest.raises(PolicyViolation, match="cannot be negative"):
        repo.set_floor("C-2024-000001", Money(-1))


def test_counting(repo: ConsignmentRepository) -> None:
    assert repo.count() == 0
    repo.add(unit())
    assert repo.count() == 1
