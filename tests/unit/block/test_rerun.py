from datetime import date

import pytest

from autozest.block.bidding import BiddingService
from autozest.block.rerun import MAX_RUNS, RerunService
from autozest.consignment.models import ConsignmentStatus
from autozest.errors import PolicyViolation
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.settlement.ledger import Account, Ledger
from tests.conftest import Floor


@pytest.fixture()
def reruns(floor: Floor) -> RerunService:
    return RerunService(floor.conn)


def missed(floor: Floor, floor_price: str = "30000.00") -> str:
    run_id = floor.catalogue(floor_price=floor_price)
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    BiddingService(floor.conn).fall(run_id)
    return floor.sales.run(run_id).consignment_id


def next_sale(floor: Floor, day: int = 19) -> str:
    return floor.sales.create(date(2024, 3, day), "Next Tuesday", {"A": 100}).code


def test_a_unit_that_never_ran(reruns: RerunService, floor: Floor) -> None:
    unit = floor.intake.register("KMHD35LEXEU000001", "d-100")
    with pytest.raises(PolicyViolation) as caught:
        reruns.plan(unit.id)
    assert caught.value.rule == "rerun.never_ran"


def test_a_plan_names_the_light_and_the_fees(reruns: RerunService, floor: Floor) -> None:
    consignment_id = missed(floor)
    planned = reruns.plan(consignment_id)
    assert planned.previous_runs == 1
    assert planned.no_sale_fee == Money.parse("110.00")
    assert planned.rerun_fee == Money.parse("55.00")
    assert planned.total_fees == Money.parse("165.00")
    assert "run 2" in planned.explain()


def test_the_light_is_worked_out_again_rather_than_carried(
    reruns: RerunService, floor: Floor
) -> None:
    consignment_id = missed(floor)
    floor.intake.consignments.set_floor(consignment_id, Money.parse("900.00"))
    planned = reruns.plan(consignment_id)
    assert planned.previous_light is Light.GREEN
    assert planned.new_light is Light.RED
    assert planned.light_changed
    assert "green to red" in planned.explain()


def test_rerunning_puts_it_in_the_next_sale(reruns: RerunService, floor: Floor) -> None:
    consignment_id = missed(floor)
    code = next_sale(floor)
    reruns.rerun(consignment_id, code, "A")
    assert floor.intake.consignments.get(consignment_id).status is ConsignmentStatus.CATALOGUED
    assert len(floor.sales.runs(code)) == 1


def test_the_seller_pays_for_the_miss_and_the_next_go(
    reruns: RerunService, floor: Floor
) -> None:
    consignment_id = missed(floor)
    reruns.rerun(consignment_id, next_sale(floor), "A")
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.SELLER_PAYABLE, "d-100") == Money.parse("-165.00")
    assert ledger.balance(Account.SERVICE_INCOME) == Money.parse("165.00")


def test_a_rerun_can_be_put_through_without_charging(
    reruns: RerunService, floor: Floor
) -> None:
    consignment_id = missed(floor)
    reruns.rerun(consignment_id, next_sale(floor), "A", charge=False)
    assert Ledger(floor.conn).balance(Account.SERVICE_INCOME) == Money.zero()


def test_only_a_no_sale_runs_again(reruns: RerunService, floor: Floor) -> None:
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    consignment_id = floor.sales.run(run_id).consignment_id
    with pytest.raises(PolicyViolation) as caught:
        reruns.rerun(consignment_id, next_sale(floor), "A")
    assert caught.value.rule == "rerun.not_a_no_sale"


def test_three_misses_and_the_floor_needs_looking_at(
    reruns: RerunService, floor: Floor
) -> None:
    consignment_id = missed(floor)
    for day in (19, 26):
        code = next_sale(floor, day)
        reruns.rerun(consignment_id, code, "A", charge=False)
        floor.sales.open_sale(code)
        run = floor.sales.runs(code)[0]
        BiddingService(floor.conn).fall(run.id or 0)
    assert len(reruns.runs_so_far(consignment_id)) == MAX_RUNS
    with pytest.raises(PolicyViolation) as caught:
        reruns.rerun(consignment_id, next_sale(floor, 28), "A", charge=False)
    assert caught.value.rule == "rerun.too_many"


def test_the_eligible_and_exhausted_lists(reruns: RerunService, floor: Floor) -> None:
    consignment_id = missed(floor)
    assert reruns.eligible() == [consignment_id]
    assert reruns.exhausted() == []


def test_a_unit_that_sold_second_time_shows_on_the_managers_list(
    reruns: RerunService, floor: Floor
) -> None:
    consignment_id = missed(floor)
    code = next_sale(floor)
    reruns.rerun(consignment_id, code, "A", charge=False)
    floor.sales.open_sale(code)
    run = floor.sales.runs(code)[0]
    floor.intake.consignments.set_floor(consignment_id, Money.parse("3000.00"))
    bidding = BiddingService(floor.conn)
    bidding.bid(run.id or 0, "d-200", Money.parse("8200.00"))
    bidding.fall(run.id or 0)
    assert reruns.sold_after_rerun() == [consignment_id]
