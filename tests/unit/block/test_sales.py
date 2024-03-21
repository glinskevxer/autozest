import sqlite3
from datetime import date

import pytest

from autozest.block.models import Lane, RunStatus, SaleStatus
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.errors import Conflict, NotFound, PolicyViolation, StateError
from autozest.inspection.announcements import Light
from autozest.vin import Vin

WHEN = date(2024, 3, 12)
VINS = [
    "1HGCM82633A004352",
    "5YJ3E1EA0KF000316",
    "JTDKN3DU8A0000001",
    "WBA3A5C52DF000001",
]


@pytest.fixture()
def yard(db: sqlite3.Connection) -> tuple[SaleRepository, IntakeService]:
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
    for index, vin in enumerate(VINS):
        vehicles.add(Vehicle(vin=Vin.parse(vin), make="Make", model=f"Model{index}"))
    return SaleRepository(db), IntakeService(db)


def consigned(intake: IntakeService, vin: str) -> str:
    unit = intake.register(vin, "d-100")
    intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
    return unit.id


def test_creating_a_sale_with_its_lanes(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, _ = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100, "B": 100})
    assert sale.code == "S-20240312"
    assert [lane.letter for lane in sales.lanes(sale.code)] == ["A", "B"]
    assert sales.get(sale.code).status is SaleStatus.PLANNED


def test_a_sale_without_lanes_is_refused(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, _ = yard
    with pytest.raises(PolicyViolation) as caught:
        sales.create(WHEN, "Empty", {})
    assert caught.value.rule == "sale.no_lanes"


def test_two_sales_cannot_share_a_day(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, _ = yard
    sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    with pytest.raises(Conflict):
        sales.create(WHEN, "Another one", {"A": 100})


def test_finding_a_sale_by_date(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, _ = yard
    sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    found = sales.on(WHEN)
    assert found is not None and found.code == "S-20240312"
    assert sales.on(date(2024, 3, 13)) is None


def test_an_unknown_sale_or_lane(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, _ = yard
    with pytest.raises(NotFound):
        sales.get("S-19990101")
    sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    with pytest.raises(NotFound):
        sales.lane("S-20240312", "Z")


def test_cataloguing_puts_units_in_order(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    for vin in VINS[:3]:
        sales.add_run(sale.code, "A", consigned(intake, vin), Light.GREEN)
    runs = sales.runs(sale.code)
    assert [run.position for run in runs] == [1, 2, 3]
    assert [run.number for run in runs] == ["A001", "A002", "A003"]


def test_a_unit_cannot_be_catalogued_twice(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    unit = consigned(intake, VINS[0])
    sales.add_run(sale.code, "A", unit, Light.GREEN)
    with pytest.raises(Conflict, match="already in this sale"):
        sales.add_run(sale.code, "A", unit, Light.GREEN)


def test_a_lane_stops_taking_units_at_its_capacity(
    yard: tuple[SaleRepository, IntakeService],
) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 2})
    for vin in VINS[:2]:
        sales.add_run(sale.code, "A", consigned(intake, vin), Light.GREEN)
    with pytest.raises(PolicyViolation) as caught:
        sales.add_run(sale.code, "A", consigned(intake, VINS[2]), Light.GREEN)
    assert caught.value.rule == "lane.full"


def test_opening_freezes_the_catalogue(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    sales.add_run(sale.code, "A", consigned(intake, VINS[0]), Light.GREEN)
    assert sales.open_sale(sale.code).status is SaleStatus.OPEN
    with pytest.raises(PolicyViolation) as caught:
        sales.add_run(sale.code, "A", consigned(intake, VINS[1]), Light.GREEN)
    assert caught.value.rule == "sale.frozen"


def test_a_lane_cannot_be_added_after_opening(
    yard: tuple[SaleRepository, IntakeService],
) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    sales.add_run(sale.code, "A", consigned(intake, VINS[0]), Light.GREEN)
    sales.open_sale(sale.code)
    with pytest.raises(PolicyViolation, match="catalogue is closed"):
        sales.add_lane(Lane(sale_code=sale.code, letter="B", capacity=50))


def test_an_empty_sale_will_not_open(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, _ = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    with pytest.raises(PolicyViolation) as caught:
        sales.open_sale(sale.code)
    assert caught.value.rule == "sale.empty"


def test_closing_turns_everything_left_into_a_no_sale(
    yard: tuple[SaleRepository, IntakeService],
) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    sales.add_run(sale.code, "A", consigned(intake, VINS[0]), Light.GREEN)
    sales.open_sale(sale.code)
    sales.close_sale(sale.code)
    assert sales.runs(sale.code)[0].status is RunStatus.NO_SALE
    assert sales.get(sale.code).status is SaleStatus.CLOSED


def test_a_sale_cannot_be_settled_before_it_closes(
    yard: tuple[SaleRepository, IntakeService],
) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    sales.add_run(sale.code, "A", consigned(intake, VINS[0]), Light.GREEN)
    sales.open_sale(sale.code)
    with pytest.raises(StateError):
        sales.settle(sale.code)
    sales.close_sale(sale.code)
    assert sales.settle(sale.code).status is SaleStatus.SETTLED


def test_pulling_a_unit_off_the_block(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    run = sales.add_run(sale.code, "A", consigned(intake, VINS[0]), Light.GREEN)
    assert run.id is not None
    assert sales.pull(run.id).status is RunStatus.PULLED


def test_finding_a_run_by_its_number(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    unit = consigned(intake, VINS[0])
    sales.add_run(sale.code, "A", unit, Light.YELLOW)
    found = sales.require_run(sale.code, "A001")
    assert found.consignment_id == unit
    assert found.light is Light.YELLOW
    with pytest.raises(NotFound):
        sales.require_run(sale.code, "A999")


def test_finding_the_run_for_a_consignment(yard: tuple[SaleRepository, IntakeService]) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    unit = consigned(intake, VINS[0])
    assert sales.run_for_consignment(unit) is None
    sales.add_run(sale.code, "A", unit, Light.GREEN)
    found = sales.run_for_consignment(unit)
    assert found is not None and found.number == "A001"


def test_require_open_is_the_guard_the_block_commands_use(
    yard: tuple[SaleRepository, IntakeService],
) -> None:
    sales, intake = yard
    sale = sales.create(WHEN, "Tuesday dealer sale", {"A": 100})
    with pytest.raises(PolicyViolation) as caught:
        sales.require_open(sale.code)
    assert caught.value.rule == "sale.not_open"
    sales.add_run(sale.code, "A", consigned(intake, VINS[0]), Light.GREEN)
    sales.open_sale(sale.code)
    assert sales.require_open(sale.code).status is SaleStatus.OPEN


def test_recent_sales_come_back_newest_first(
    yard: tuple[SaleRepository, IntakeService],
) -> None:
    sales, _ = yard
    sales.create(date(2024, 3, 5), "First", {"A": 10})
    sales.create(date(2024, 3, 12), "Second", {"A": 10})
    assert [s.title for s in sales.recent()] == ["Second", "First"]
