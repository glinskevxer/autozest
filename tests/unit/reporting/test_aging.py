from datetime import datetime, timedelta

import pytest

from autozest.block.bidding import BiddingService
from autozest.money import Money
from autozest.reporting import aging
from autozest.reporting.aging import BUCKETS, bucket_names
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.statements import Side
from tests.conftest import Floor


def sold_and_posted(floor: Floor, price: str = "8200.00") -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    PostingService(floor.conn).post_sale(run_id)
    return run_id


def test_an_empty_book_ages_to_nothing(floor: Floor) -> None:
    report = aging.build(floor.conn)
    assert report.parties == ()
    assert report.total == Money.zero()
    assert report.overdue == ()


def test_a_fresh_charge_is_current(floor: Floor) -> None:
    sold_and_posted(floor)
    report = aging.build(floor.conn)
    assert report.total == Money.parse("8545.00")
    assert report.bucket("current") == Money.parse("8545.00")
    assert not report.parties[0].is_overdue


def test_an_old_charge_falls_into_a_later_bucket(floor: Floor) -> None:
    sold_and_posted(floor)
    later = datetime.now().astimezone() + timedelta(days=45)
    report = aging.build(floor.conn, as_at=later)
    assert report.bucket("over 30") == Money.parse("8545.00")
    assert report.bucket("current") == Money.zero()
    assert len(report.overdue) == 1


@pytest.mark.parametrize(
    ("days", "bucket"),
    [(1, "current"), (6, "3 to 7"), (13, "8 to 14"), (25, "15 to 30"), (60, "over 30")],
)
def test_the_buckets_line_up_with_business_days(floor: Floor, days: int, bucket: str) -> None:
    sold_and_posted(floor)
    later = datetime.now().astimezone() + timedelta(days=days * 7 // 5 + 2)
    report = aging.build(floor.conn, as_at=later)
    assert report.bucket(bucket).cents != 0 or bucket == "current"


def test_a_payment_nets_the_party_off_the_list(floor: Floor) -> None:
    sold_and_posted(floor)
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    assert aging.build(floor.conn).parties == ()


def test_a_part_payment_leaves_the_rest_aged(floor: Floor) -> None:
    sold_and_posted(floor)
    PaymentService(floor.conn).receive("d-200", Money.parse("5000.00"))
    report = aging.build(floor.conn)
    assert report.total == Money.parse("3545.00")
    assert report.parties[0].party_id == "d-200"


def test_the_seller_side_ages_the_other_way(floor: Floor) -> None:
    sold_and_posted(floor)
    report = aging.build(floor.conn, Side.SELLER)
    assert report.total == Money.parse("7965.00")
    assert report.parties[0].party_id == "d-100"


def test_a_charge_posted_in_the_future_is_not_negative_days(floor: Floor) -> None:
    sold_and_posted(floor)
    earlier = datetime.now().astimezone() - timedelta(days=10)
    report = aging.build(floor.conn, as_at=earlier)
    assert report.parties[0].oldest == 0


def test_the_bucket_totals_add_back_to_the_whole(floor: Floor) -> None:
    sold_and_posted(floor)
    report = aging.build(floor.conn)
    assert Money(sum(m.cents for m in report.totals().values())) == report.total


def test_the_bucket_names_are_stable() -> None:
    assert bucket_names() == ["current", "3 to 7", "8 to 14", "15 to 30", "over 30"]
    assert BUCKETS[-1][1] is None
