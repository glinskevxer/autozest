import sqlite3

import pytest

from autozest.block.bidding import BiddingService
from autozest.block.models import RunStatus
from autozest.block.proxy import ProxyService
from autozest.errors import Conflict, PolicyViolation, ValidationError
from autozest.money import Money
from autozest.storage.db import table_names
from tests.conftest import Floor


@pytest.fixture()
def proxies(floor: Floor) -> ProxyService:
    return ProxyService(floor.conn)


def scheduled(floor: Floor, floor_price: str = "5000.00") -> int:
    return floor.catalogue(floor_price=floor_price)


def test_the_proxy_table_is_there(db: sqlite3.Connection) -> None:
    assert "proxy_bid" in table_names(db)


def test_leaving_a_maximum(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    left = proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    assert left.maximum == Money.parse("12000.00")
    assert left.is_live
    assert len(proxies.live(run_id)) == 1


def test_a_proxy_of_nothing(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    with pytest.raises(ValidationError, match="for something"):
        proxies.leave(run_id, "d-200", Money.zero())


def test_a_seller_cannot_leave_one_on_their_own_unit(
    floor: Floor, proxies: ProxyService
) -> None:
    run_id = scheduled(floor)
    with pytest.raises(PolicyViolation) as caught:
        proxies.leave(run_id, "d-100", Money.parse("12000.00"))
    assert caught.value.rule == "bid.own_unit"


def test_one_proxy_per_buyer_per_run(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    with pytest.raises(Conflict):
        proxies.leave(run_id, "d-200", Money.parse("14000.00"))


def test_a_withdrawn_proxy_frees_the_slot(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    proxies.withdraw(run_id, "d-200")
    assert proxies.live(run_id) == []
    proxies.leave(run_id, "d-200", Money.parse("14000.00"))


def test_a_proxy_after_the_unit_ran(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    floor.sales.open_sale(floor.code)
    BiddingService(floor.conn).fall(run_id)
    with pytest.raises(PolicyViolation) as caught:
        proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    assert caught.value.rule == "proxy.too_late"


def test_one_proxy_against_an_empty_room_pays_its_maximum(
    floor: Floor, proxies: ProxyService
) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    outcome = proxies.settle(run_id)
    assert outcome.price == Money.parse("12000.00")


def test_a_proxy_pays_one_step_over_the_room_not_its_maximum(
    floor: Floor, proxies: ProxyService
) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    outcome = proxies.settle(run_id, Money.parse("9200.00"))
    assert outcome.price == Money.parse("9300.00")
    assert outcome.winner is not None and outcome.winner.buyer_id == "d-200"


def test_a_proxy_never_pays_past_its_maximum(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("9250.00"))
    outcome = proxies.settle(run_id, Money.parse("9200.00"))
    assert outcome.price == Money.parse("9250.00")


def test_a_room_that_beats_the_proxy_wins(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("9000.00"))
    outcome = proxies.settle(run_id, Money.parse("9200.00"))
    assert not outcome.has_winner


def test_two_proxies_settle_between_themselves(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("9000.00"))
    proxies.leave(run_id, "d-300", Money.parse("12000.00"))
    outcome = proxies.settle(run_id)
    assert outcome.winner is not None and outcome.winner.buyer_id == "d-300"
    assert outcome.price == Money.parse("9100.00")
    assert len(outcome.beaten) == 1


def test_two_identical_proxies_go_to_the_earlier_one(
    floor: Floor, proxies: ProxyService
) -> None:
    run_id = scheduled(floor)
    first = proxies.leave(run_id, "d-200", Money.parse("9000.00"))
    proxies.leave(run_id, "d-300", Money.parse("9000.00"))
    outcome = proxies.settle(run_id)
    assert outcome.winner is not None and outcome.winner.id == first.id
    assert outcome.price == Money.parse("9000.00")


def test_executing_bids_the_proxy_in_and_sells(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    floor.sales.open_sale(floor.code)
    run = proxies.execute(run_id)
    assert run.status is RunStatus.SOLD
    assert run.hammer == Money.parse("12000.00")
    assert run.buyer_id == "d-200"


def test_executing_with_a_room_bid_standing(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor)
    proxies.leave(run_id, "d-200", Money.parse("12000.00"))
    floor.sales.open_sale(floor.code)
    BiddingService(floor.conn).bid(run_id, "d-300", Money.parse("9200.00"))
    run = proxies.execute(run_id)
    assert run.hammer == Money.parse("9300.00")
    assert run.buyer_id == "d-200"


def test_executing_with_no_proxies_just_drops_the_hammer(
    floor: Floor, proxies: ProxyService
) -> None:
    run_id = scheduled(floor)
    floor.sales.open_sale(floor.code)
    assert proxies.execute(run_id).status is RunStatus.NO_SALE


def test_a_proxy_under_the_floor_still_goes_on_if(floor: Floor, proxies: ProxyService) -> None:
    run_id = scheduled(floor, "10000.00")
    proxies.leave(run_id, "d-200", Money.parse("8000.00"))
    floor.sales.open_sale(floor.code)
    assert proxies.execute(run_id).status is RunStatus.IF_BID
