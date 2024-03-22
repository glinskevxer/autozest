from decimal import Decimal

import pytest

from autozest.block.catalogue import CatalogueService, order_runs
from autozest.catalog.accounts import AccountClass
from autozest.consignment.models import Consignment, ConsignmentStatus
from autozest.errors import PolicyViolation
from autozest.inspection.announcements import AnnouncementCode, Light
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.money import Money
from tests.unit.block.conftest import VINS, Floor


@pytest.fixture()
def catalogue(floor: Floor) -> CatalogueService:
    return CatalogueService(floor.conn)


def inspected(
    floor: Floor,
    index: int = 0,
    *,
    seller: str = "d-100",
    floor_price: str | None = "5000.00",
    damage: DamageLine | None = None,
    drivable: bool = True,
) -> str:
    from autozest.consignment.intake import Arrival

    unit = floor.intake.register(
        VINS[index], seller, floor=Money.parse(floor_price) if floor_price else None
    )
    floor.intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
    inspection = floor.intake.consignments
    service = CatalogueService(floor.conn).inspection
    report = service.open_report(unit.id, "j.hale")
    if damage is not None:
        service.add_damage(report.id, damage)
    service.finalise(report.id, drivable=drivable, reference_value=Money.parse("12000.00"))
    assert inspection.get(unit.id).status is ConsignmentStatus.INSPECTED
    return unit.id


def test_a_clean_unit_is_catalogued_green(floor: Floor, catalogue: CatalogueService) -> None:
    unit = inspected(floor)
    placement = catalogue.place(floor.code, unit, "A")
    assert placement.light is Light.GREEN
    assert placement.position == 1
    assert floor.sales.runs(floor.code)[0].light is Light.GREEN


def test_structural_damage_reaches_the_light_through_the_announcement(
    floor: Floor, catalogue: CatalogueService
) -> None:
    unit = inspected(
        floor,
        damage=DamageLine(
            panel=Panel.RAIL, kind=DamageKind.BENT, severity=2, estimate=Money.parse("100.00")
        ),
    )
    placement = catalogue.place(floor.code, unit, "A")
    assert placement.light is Light.YELLOW
    assert AnnouncementCode.UNIBODY_DAMAGE in catalogue.inspection.announcements(unit)


def test_a_cheap_unit_is_catalogued_red(floor: Floor, catalogue: CatalogueService) -> None:
    unit = inspected(floor, floor_price="900.00")
    assert catalogue.place(floor.code, unit, "A").light is Light.RED


def test_a_guarantee_lifts_the_price_band(floor: Floor, catalogue: CatalogueService) -> None:
    unit = inspected(floor, floor_price="900.00")
    placement = catalogue.place(floor.code, unit, "A", guaranteed=True)
    assert placement.light is Light.GREEN
    assert "the price band" in placement.decision.relaxed


def test_a_unit_that_will_not_move_is_red(floor: Floor, catalogue: CatalogueService) -> None:
    unit = inspected(floor, drivable=False)
    assert catalogue.place(floor.code, unit, "A").light is Light.RED


def test_cataloguing_moves_the_consignment_on(
    floor: Floor, catalogue: CatalogueService
) -> None:
    unit = inspected(floor)
    catalogue.place(floor.code, unit, "A")
    assert floor.intake.consignments.get(unit).status is ConsignmentStatus.CATALOGUED


def test_a_unit_without_a_signed_report_is_not_catalogued(
    floor: Floor, catalogue: CatalogueService
) -> None:
    from autozest.consignment.intake import Arrival

    unit = floor.intake.register(VINS[0], "d-100")
    floor.intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
    with pytest.raises(PolicyViolation) as caught:
        catalogue.place(floor.code, unit.id, "A")
    assert caught.value.rule == "catalogue.not_inspected"


def test_ready_lists_what_is_waiting(floor: Floor, catalogue: CatalogueService) -> None:
    first = inspected(floor, 0)
    inspected(floor, 1)
    assert len(catalogue.ready()) == 2
    catalogue.place(floor.code, first, "A")
    assert len(catalogue.ready()) == 1


def test_filling_a_lane_places_everything_ready(
    floor: Floor, catalogue: CatalogueService
) -> None:
    for index in range(3):
        inspected(floor, index)
    placed = catalogue.fill(floor.code, "A")
    assert [p.position for p in placed] == [1, 2, 3]
    assert catalogue.ready() == []


def test_the_lane_summary_counts_the_lights(floor: Floor, catalogue: CatalogueService) -> None:
    inspected(floor, 0)
    inspected(floor, 1, floor_price="900.00")
    catalogue.fill(floor.code, "A")
    summary = catalogue.lane_summary(floor.code, "A")
    assert summary[Light.GREEN] == 1
    assert summary[Light.RED] == 1
    assert summary[Light.BLUE] == 0


def test_a_light_can_be_moved_by_hand_before_the_sale_opens(
    floor: Floor, catalogue: CatalogueService
) -> None:
    unit = inspected(floor)
    placement = catalogue.place(floor.code, unit, "A")
    run = floor.sales.run_at(floor.code, placement.lane, placement.position)
    assert run.id is not None
    moved = catalogue.relight(run.id, Light.YELLOW, "seller asked for it")
    assert moved.light is Light.YELLOW


def test_moving_a_light_needs_a_reason(floor: Floor, catalogue: CatalogueService) -> None:
    unit = inspected(floor)
    placement = catalogue.place(floor.code, unit, "A")
    run = floor.sales.run_at(floor.code, placement.lane, placement.position)
    assert run.id is not None
    with pytest.raises(PolicyViolation) as caught:
        catalogue.relight(run.id, Light.YELLOW, "  ")
    assert caught.value.rule == "catalogue.relight_reason"


def test_a_light_does_not_move_once_the_sale_is_open(
    floor: Floor, catalogue: CatalogueService
) -> None:
    unit = inspected(floor)
    placement = catalogue.place(floor.code, unit, "A")
    floor.sales.open_sale(floor.code)
    run = floor.sales.run_at(floor.code, placement.lane, placement.position)
    assert run.id is not None
    with pytest.raises(PolicyViolation) as caught:
        catalogue.relight(run.id, Light.YELLOW, "seller asked for it")
    assert caught.value.rule == "catalogue.frozen"


def unit(reference: str, seller: str) -> Consignment:
    return Consignment(id=reference, vin="x", seller_id=seller)


def test_the_run_order_spreads_a_seller_out() -> None:
    units = [unit(f"C-2024-00000{i}", "d-100" if i < 4 else "d-200") for i in range(1, 7)]
    lights = dict.fromkeys((u.id for u in units), Light.GREEN)
    ordered = order_runs(units, lights)
    sellers = [u.seller_id for u in ordered]
    assert sellers[0] != sellers[1]


def test_the_run_order_keeps_red_out_of_the_front() -> None:
    units = [unit(f"C-2024-00000{i}", f"d-{i}") for i in range(1, 9)]
    lights = {u.id: Light.GREEN for u in units}
    lights[units[0].id] = Light.RED
    ordered = order_runs(units, lights)
    assert ordered[0].id != units[0].id
    assert units[0].id in [u.id for u in ordered]


def test_a_lane_of_nothing_but_red_still_gets_ordered() -> None:
    units = [unit(f"C-2024-00000{i}", f"d-{i}") for i in range(1, 4)]
    lights = dict.fromkeys((u.id for u in units), Light.RED)
    assert len(order_runs(units, lights)) == 3


def test_a_lane_of_one_seller_still_gets_ordered() -> None:
    units = [unit(f"C-2024-00000{i}", "d-100") for i in range(1, 4)]
    lights = dict.fromkeys((u.id for u in units), Light.GREEN)
    assert [u.id for u in order_runs(units, lights)] == [u.id for u in units]


def test_the_order_is_stable_for_the_same_input() -> None:
    units = [unit(f"C-2024-00000{i}", f"d-{i % 2}") for i in range(1, 7)]
    lights = dict.fromkeys((u.id for u in units), Light.GREEN)
    assert [u.id for u in order_runs(units, lights)] == [
        u.id for u in order_runs(units, lights)
    ]


def test_a_finance_seller_never_gets_green(floor: Floor, catalogue: CatalogueService) -> None:
    unit_id = inspected(floor, seller="f-1")
    placement = catalogue.place(floor.code, unit_id, "A")
    assert placement.light is Light.YELLOW
    assert AccountClass.FINANCE.value in placement.decision.explain()


def test_the_decision_carries_the_grade_it_used(
    floor: Floor, catalogue: CatalogueService
) -> None:
    unit_id = inspected(
        floor,
        damage=DamageLine(
            panel=Panel.HOOD, kind=DamageKind.DENT, severity=3, estimate=Money.parse("4000.00")
        ),
    )
    consignment = floor.intake.consignments.get(unit_id)
    decision = catalogue.decide_light(consignment)
    report = catalogue.inspection.latest_for(unit_id)
    assert report is not None and report.grade == Decimal("2.3")
    assert decision.light is Light.YELLOW
