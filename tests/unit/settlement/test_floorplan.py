import pytest

from autozest.block.bidding import BiddingService
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from autozest.money import Money
from autozest.settlement.floorplan import FloorplanService, LineStatus
from autozest.settlement.postings import PostingService
from tests.conftest import Floor


@pytest.fixture()
def floorplan(floor: Floor) -> FloorplanService:
    return FloorplanService(floor.conn)


def line(floorplan: FloorplanService, limit: str = "50000.00") -> str:
    floorplan.open_line("L-1", "d-200", "Ninth Street Finance", Money.parse(limit))
    return "L-1"


def sold(floor: Floor, price: str = "8200.00") -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    return run_id


def test_a_buyer_with_no_line_is_not_stopped(floorplan: FloorplanService) -> None:
    capacity = floorplan.capacity("d-200")
    assert capacity.limit == Money.zero()
    floorplan.require_capacity("d-200", Money.parse("90000.00"))


def test_opening_a_line(floorplan: FloorplanService) -> None:
    line(floorplan)
    found = floorplan.line("L-1")
    assert found.limit == Money.parse("50000.00")
    assert found.status is LineStatus.ACTIVE
    assert found.opened_on is not None


def test_a_negative_limit(floorplan: FloorplanService) -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        floorplan.open_line("L-2", "d-200", "Lender", Money(-1))


def test_an_unknown_line(floorplan: FloorplanService) -> None:
    with pytest.raises(NotFound):
        floorplan.line("L-9")


def test_the_same_buyer_and_lender_only_once(floorplan: FloorplanService) -> None:
    line(floorplan)
    with pytest.raises(Conflict):
        floorplan.open_line("L-2", "d-200", "Ninth Street Finance", Money.parse("1000.00"))


def test_drawing_reduces_what_is_left(floorplan: FloorplanService) -> None:
    line(floorplan)
    floorplan.draw("L-1", Money.parse("12000.00"))
    capacity = floorplan.capacity("d-200")
    assert capacity.drawn == Money.parse("12000.00")
    assert capacity.available == Money.parse("38000.00")
    assert "38,000.00 of 50,000.00" in capacity.explain()


def test_repaying_puts_it_back(floorplan: FloorplanService) -> None:
    line(floorplan)
    drawn = floorplan.draw("L-1", Money.parse("12000.00"))
    floorplan.repay(drawn.id)
    assert floorplan.capacity("d-200").available == Money.parse("50000.00")
    assert floorplan.outstanding_for("d-200") == Money.zero()


def test_what_is_owed_on_account_counts_against_the_line(
    floor: Floor, floorplan: FloorplanService
) -> None:
    line(floorplan)
    PostingService(floor.conn).post_sale(sold(floor))
    capacity = floorplan.capacity("d-200")
    assert capacity.on_account == Money.parse("8545.00")
    assert capacity.available == Money.parse("41455.00")


def test_going_past_the_line_is_refused(floorplan: FloorplanService) -> None:
    line(floorplan, "10000.00")
    with pytest.raises(PolicyViolation) as caught:
        floorplan.require_capacity("d-200", Money.parse("12000.00"))
    assert caught.value.rule == "credit.exhausted"
    assert "past their line" in str(caught.value)


def test_spending_right_up_to_the_line_is_allowed(floorplan: FloorplanService) -> None:
    line(floorplan, "10000.00")
    floorplan.require_capacity("d-200", Money.parse("10000.00"))


def test_a_suspended_line_stops_counting(floorplan: FloorplanService) -> None:
    line(floorplan)
    floorplan.set_status("L-1", LineStatus.SUSPENDED)
    assert floorplan.capacity("d-200").limit == Money.zero()
    assert not LineStatus.SUSPENDED.can_draw


def test_drawing_on_a_suspended_line(floorplan: FloorplanService) -> None:
    line(floorplan)
    floorplan.set_status("L-1", LineStatus.SUSPENDED)
    with pytest.raises(PolicyViolation) as caught:
        floorplan.draw("L-1", Money.parse("100.00"))
    assert caught.value.rule == "credit.line_closed"


def test_moving_a_limit(floorplan: FloorplanService) -> None:
    line(floorplan)
    assert floorplan.set_limit("L-1", Money.parse("75000.00")).limit == Money.parse("75000.00")
    with pytest.raises(ValidationError):
        floorplan.set_limit("L-1", Money(-1))


def test_two_lenders_add_up(floorplan: FloorplanService) -> None:
    line(floorplan, "20000.00")
    floorplan.open_line("L-2", "d-200", "Second Lender", Money.parse("30000.00"))
    assert floorplan.capacity("d-200").limit == Money.parse("50000.00")


def test_a_drawing_is_tied_to_a_run_once(floor: Floor, floorplan: FloorplanService) -> None:
    line(floorplan)
    run_id = sold(floor)
    floorplan.draw("L-1", Money.parse("8545.00"), run_id=run_id)
    assert floorplan.draw_for_run(run_id) is not None
    with pytest.raises(Conflict, match="already been drawn"):
        floorplan.draw("L-1", Money.parse("8545.00"), run_id=run_id)


def test_a_drawing_of_nothing(floorplan: FloorplanService) -> None:
    line(floorplan)
    with pytest.raises(ValidationError, match="for something"):
        floorplan.draw("L-1", Money.zero())


def test_an_exhausted_line_reports_itself(floorplan: FloorplanService) -> None:
    line(floorplan, "10000.00")
    floorplan.draw("L-1", Money.parse("10000.00"))
    assert floorplan.capacity("d-200").is_exhausted
    assert floorplan.capacity("d-200").available == Money.zero()
