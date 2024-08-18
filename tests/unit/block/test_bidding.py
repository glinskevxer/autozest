import pytest

from autozest.block.bidding import BiddingService, BidKind, increment_at
from autozest.block.models import RunStatus
from autozest.block.registration import RegistrationService
from autozest.consignment.models import ConsignmentStatus
from autozest.errors import PolicyViolation, ValidationError
from autozest.money import Money
from autozest.settlement.floorplan import FloorplanService
from tests.conftest import Floor


@pytest.fixture()
def bidding(floor: Floor) -> BiddingService:
    return BiddingService(floor.conn)


def opened(floor: Floor, **kwargs: object) -> int:
    run_id = floor.catalogue(**kwargs)
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    return run_id


def test_the_increment_follows_the_band() -> None:
    assert increment_at(Money.parse("400.00")) == Money.parse("25.00")
    assert increment_at(Money.parse("1000.00")) == Money.parse("50.00")
    assert increment_at(Money.parse("9000.00")) == Money.parse("100.00")
    assert increment_at(Money.parse("22000.00")) == Money.parse("250.00")
    assert increment_at(Money.parse("80000.00")) == Money.parse("500.00")


def test_a_first_bid_can_be_anything(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bid = bidding.bid(run_id, "d-200", Money.parse("100.00"))
    assert bid.amount == Money.parse("100.00")
    assert bid.kind is BidKind.LIVE


def test_a_bid_below_the_next_step_is_refused(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("4000.00"))
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "d-200", Money.parse("4025.00"))
    assert caught.value.rule == "bid.increment"
    assert "4,050.00" in str(caught.value)


def test_a_bid_exactly_on_the_step_is_taken(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("4000.00"))
    bidding.bid(run_id, "d-200", Money.parse("4050.00"))
    standing = bidding.standing_bid(run_id)
    assert standing is not None and standing.amount == Money.parse("4050.00")


def test_a_negative_bid(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    with pytest.raises(ValidationError):
        bidding.bid(run_id, "d-200", Money(-1))


def test_a_seller_cannot_bid_on_their_own_unit(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "d-100", Money.parse("4000.00"))
    assert caught.value.rule == "bid.own_unit"


def test_a_finance_account_cannot_bid(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "f-1", Money.parse("4000.00"))
    assert caught.value.rule == "account.bid"


def test_bidding_before_the_sale_opens(floor: Floor, bidding: BiddingService) -> None:
    run_id = floor.catalogue()
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "d-200", Money.parse("4000.00"))
    assert caught.value.rule == "sale.not_open"


def test_a_bid_over_the_floor_sells(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor, floor_price="5000.00")
    bidding.bid(run_id, "d-200", Money.parse("5200.00"))
    run = bidding.fall(run_id)
    assert run.status is RunStatus.SOLD
    assert run.hammer == Money.parse("5200.00")
    assert run.buyer_id == "d-200"


def test_a_sold_unit_closes_its_consignment(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("5200.00"))
    run = bidding.fall(run_id)
    consignment = floor.intake.consignments.get(run.consignment_id)
    assert consignment.status is ConsignmentStatus.SOLD


def test_a_bid_under_the_floor_goes_on_if(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor, floor_price="5000.00")
    bidding.bid(run_id, "d-200", Money.parse("4200.00"))
    run = bidding.fall(run_id)
    assert run.status is RunStatus.IF_BID
    assert run.hammer == Money.parse("4200.00")
    assert bidding.pending_if_bids(floor.code) == [run]


def test_a_unit_with_no_floor_is_on_the_market(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor, floor_price=None)
    bidding.bid(run_id, "d-200", Money.parse("900.00"))
    assert bidding.fall(run_id).status is RunStatus.SOLD


def test_no_bid_at_all_is_a_no_sale(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    run = bidding.fall(run_id)
    assert run.status is RunStatus.NO_SALE
    assert run.hammer is None


def test_a_unit_only_goes_through_once(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.fall(run_id)
    with pytest.raises(PolicyViolation) as caught:
        bidding.fall(run_id)
    assert caught.value.rule == "run.finished"


def test_the_seller_accepting_an_if_bid(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("4200.00"))
    bidding.fall(run_id)
    run = bidding.accept(run_id)
    assert run.status is RunStatus.SOLD
    assert run.hammer == Money.parse("4200.00")


def test_a_counter_the_buyer_takes(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("4200.00"))
    bidding.fall(run_id)
    bidding.counter(run_id, Money.parse("4600.00"))
    run = bidding.accept(run_id)
    assert run.hammer == Money.parse("4600.00")


def test_a_counter_has_to_be_above_the_bid(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("4200.00"))
    bidding.fall(run_id)
    with pytest.raises(PolicyViolation) as caught:
        bidding.counter(run_id, Money.parse("4100.00"))
    assert caught.value.rule == "if_bid.counter_below"


def test_a_buyer_walking_away_leaves_a_no_sale(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("4200.00"))
    bidding.fall(run_id)
    bidding.counter(run_id, Money.parse("4600.00"))
    run = bidding.decline(run_id)
    assert run.status is RunStatus.NO_SALE
    assert run.hammer is None
    assert run.buyer_id is None


def test_countering_something_that_is_not_on_if(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    with pytest.raises(PolicyViolation) as caught:
        bidding.counter(run_id, Money.parse("4600.00"))
    assert caught.value.rule == "if_bid.not_pending"


def test_the_bid_history_keeps_the_losers(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    for amount in ("4000.00", "4100.00", "4200.00"):
        bidding.bid(run_id, "d-200", Money.parse(amount))
    bidding.fall(run_id)
    history = bidding.bids(run_id)
    assert [str(bid.amount) for bid in history] == ["4,000.00", "4,100.00", "4,200.00"]


def test_a_bid_can_still_be_taken_while_a_unit_is_on_if(
    floor: Floor, bidding: BiddingService
) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("4200.00"))
    bidding.fall(run_id)
    bidding.bid(run_id, "d-200", Money.parse("4400.00"))
    assert bidding.accept(run_id).hammer == Money.parse("4400.00")


def test_a_buyer_with_no_line_bids_freely(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("90000.00"))
    assert bidding.fall(run_id).status is RunStatus.SOLD


def test_a_bid_past_the_line_is_refused_at_the_bid(
    floor: Floor, bidding: BiddingService
) -> None:
    run_id = opened(floor)
    FloorplanService(floor.conn).open_line(
        "L-1", "d-200", "Ninth Street Finance", Money.parse("5000.00")
    )
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "d-200", Money.parse("8000.00"))
    assert caught.value.rule == "credit.exhausted"
    assert caught.value.context["run"] == "A001"


def test_a_bid_inside_the_line_is_taken(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    FloorplanService(floor.conn).open_line(
        "L-1", "d-200", "Ninth Street Finance", Money.parse("20000.00")
    )
    bidding.bid(run_id, "d-200", Money.parse("8000.00"))
    assert bidding.fall(run_id).status is RunStatus.SOLD


def test_what_is_already_drawn_narrows_what_can_be_bid(
    floor: Floor, bidding: BiddingService
) -> None:
    run_id = opened(floor)
    credit = FloorplanService(floor.conn)
    credit.open_line("L-1", "d-200", "Ninth Street Finance", Money.parse("20000.00"))
    credit.draw("L-1", Money.parse("15000.00"))
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "d-200", Money.parse("8000.00"))
    assert "5,000.00" in str(caught.value)


def test_a_sale_with_no_door_list_does_not_enforce_one(
    floor: Floor, bidding: BiddingService
) -> None:
    run_id = opened(floor)
    bidding.bid(run_id, "d-200", Money.parse("5200.00"))
    assert bidding.fall(run_id).status is RunStatus.SOLD


def test_once_one_buyer_registers_everybody_has_to(
    floor: Floor, bidding: BiddingService
) -> None:
    run_id = opened(floor)
    RegistrationService(floor.conn).register(floor.code, "d-300")
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(run_id, "d-200", Money.parse("5200.00"))
    assert caught.value.rule == "registration.missing"


def test_a_registered_buyer_bids(floor: Floor, bidding: BiddingService) -> None:
    run_id = opened(floor)
    RegistrationService(floor.conn).register(floor.code, "d-200")
    bidding.bid(run_id, "d-200", Money.parse("5200.00"))
    assert bidding.fall(run_id).status is RunStatus.SOLD
