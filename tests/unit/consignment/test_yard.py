import sqlite3

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import IntakeService
from autozest.consignment.yard import Space, SpaceStatus, YardService
from autozest.errors import Conflict, NotFound, PolicyViolation
from autozest.storage.db import table_names
from autozest.vin import Vin

VINS = ["1HGCM82633A004352", "5YJ3E1EA0KF000316"]


@pytest.fixture()
def yard(db: sqlite3.Connection) -> YardService:
    AccountRepository(db).add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-1",
            state="TX",
        )
    )
    vehicles = VehicleRepository(db)
    for index, vin in enumerate(VINS):
        vehicles.add(Vehicle(vin=Vin.parse(vin), make="Make", model=f"Model{index}"))
    service = YardService(db)
    service.add_space(Space(id="s-1", zone="north", row_label="C", number=14))
    service.add_space(Space(id="s-2", zone="north", row_label="C", number=15, covered=True))
    return service


def consigned(yard: YardService, index: int = 0) -> str:
    return IntakeService(yard.conn).register(VINS[index], "d-100").id


def test_the_yard_tables_are_there(db: sqlite3.Connection) -> None:
    assert "yard_space" in table_names(db)
    assert "yard_movement" in table_names(db)


def test_a_space_labels_itself_the_way_the_yard_calls_it(yard: YardService) -> None:
    assert yard.space("s-1").label == "north-C014"


def test_two_spaces_cannot_share_a_number(yard: YardService) -> None:
    with pytest.raises(Conflict):
        yard.add_space(Space(id="s-3", zone="north", row_label="C", number=14))


def test_an_unknown_space(yard: YardService) -> None:
    with pytest.raises(NotFound):
        yard.space("s-9")


def test_parking_a_unit(yard: YardService) -> None:
    unit = consigned(yard)
    movement = yard.put(unit, "s-1", "r.okafor", reason="off the truck")
    assert movement.space_id == "s-1"
    assert yard.where_is(unit) == "s-1"
    assert yard.occupant("s-1") == unit
    assert yard.space("s-1").status is SpaceStatus.OCCUPIED


def test_a_space_holds_one_unit(yard: YardService) -> None:
    first = consigned(yard, 0)
    second = consigned(yard, 1)
    yard.put(first, "s-1", "r.okafor")
    with pytest.raises(Conflict) as caught:
        yard.put(second, "s-1", "r.okafor")
    assert caught.value.context["occupant"] == first


def test_moving_a_unit_frees_the_old_space(yard: YardService) -> None:
    unit = consigned(yard)
    yard.put(unit, "s-1", "r.okafor")
    yard.put(unit, "s-2", "r.okafor", reason="wanted it under cover")
    assert yard.space("s-1").status is SpaceStatus.FREE
    assert yard.where_is(unit) == "s-2"
    assert len(yard.history(unit)) == 2


def test_taking_a_unit_out(yard: YardService) -> None:
    unit = consigned(yard)
    yard.put(unit, "s-1", "r.okafor")
    movement = yard.take_out(unit, "r.okafor", "collected by the buyer")
    assert movement.is_departure
    assert yard.where_is(unit) is None
    assert yard.space("s-1").status is SpaceStatus.FREE
    assert yard.occupant("s-1") is None


def test_taking_out_something_that_is_not_here(yard: YardService) -> None:
    unit = consigned(yard)
    with pytest.raises(PolicyViolation) as caught:
        yard.take_out(unit, "r.okafor", "gone")
    assert caught.value.rule == "yard.not_here"


def test_free_spaces_narrow_by_zone_and_cover(yard: YardService) -> None:
    assert len(yard.free_spaces()) == 2
    assert len(yard.free_spaces("north")) == 2
    assert len(yard.free_spaces("south")) == 0
    assert [s.id for s in yard.free_spaces(covered=True)] == ["s-2"]


def test_blocking_a_space_takes_it_out_of_use(yard: YardService) -> None:
    yard.block("s-1", "resurfacing")
    assert yard.space("s-1").status is SpaceStatus.BLOCKED
    assert [s.id for s in yard.free_spaces()] == ["s-2"]
    unit = consigned(yard)
    with pytest.raises(Conflict):
        yard.put(unit, "s-1", "r.okafor")
    yard.unblock("s-1")
    yard.put(unit, "s-1", "r.okafor")


def test_an_occupied_space_cannot_be_blocked(yard: YardService) -> None:
    unit = consigned(yard)
    yard.put(unit, "s-1", "r.okafor")
    with pytest.raises(PolicyViolation) as caught:
        yard.block("s-1", "resurfacing")
    assert caught.value.rule == "yard.occupied"


def test_the_occupancy_summary(yard: YardService) -> None:
    unit = consigned(yard)
    yard.put(unit, "s-1", "r.okafor")
    assert yard.capacity() == 2
    counts = yard.occupancy()
    assert counts[SpaceStatus.OCCUPIED] == 1
    assert counts[SpaceStatus.FREE] == 1
