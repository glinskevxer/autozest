from decimal import Decimal

from autozest.block.bidding import BiddingService
from autozest.money import Money
from autozest.reporting import concentration
from autozest.reporting.concentration import WARN_ABOVE
from tests.conftest import Floor


def run_sale(floor: Floor, sales: list[tuple[str, str]]) -> None:
    run_ids = [floor.catalogue(floor_price="3000.00") for _ in sales]
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    for run_id, (buyer, price) in zip(run_ids, sales, strict=True):
        bidding.bid(run_id, buyer, Money.parse(price))
        bidding.fall(run_id)


def test_a_sale_with_nothing_sold(floor: Floor) -> None:
    floor.catalogue()
    floor.sales.open_sale(floor.code)
    report = concentration.build(floor.conn, floor.code)
    assert report.buyers == ()
    assert "nothing sold" in report.summary()
    assert not report.is_concentrated


def test_two_buyers_splitting_a_sale(floor: Floor) -> None:
    run_sale(floor, [("d-200", "6000.00"), ("d-300", "6000.00")])
    report = concentration.build(floor.conn, floor.code)
    assert report.gross == Money.parse("12000.00")
    assert [share.share for share in report.buyers] == [Decimal("50.0"), Decimal("50.0")]
    assert report.is_concentrated


def test_the_heaviest_buyer_comes_first(floor: Floor) -> None:
    run_sale(floor, [("d-200", "9000.00"), ("d-300", "3000.00")])
    report = concentration.build(floor.conn, floor.code)
    top = report.heaviest_buyer
    assert top is not None
    assert top.party_id == "d-200"
    assert top.share == Decimal("75.0")
    assert top.units == 1


def test_a_light_buyer_is_not_flagged(floor: Floor) -> None:
    run_sale(
        floor,
        [
            ("d-200", "3000.00"),
            ("d-300", "3000.00"),
            ("d-200", "3000.00"),
            ("d-300", "3000.00"),
        ],
    )
    report = concentration.build(floor.conn, floor.code)
    assert all(share.share == Decimal("50.0") for share in report.buyers)
    assert Decimal("30.0") == WARN_ABOVE


def test_one_seller_shows_on_the_seller_side(floor: Floor) -> None:
    run_sale(floor, [("d-200", "6000.00"), ("d-300", "6000.00")])
    report = concentration.build(floor.conn, floor.code)
    assert len(report.sellers) == 1
    assert report.sellers[0].party_id == "d-100"
    assert report.sellers[0].share == Decimal("100.0")
    assert len(report.heavy_sellers) == 1


def test_a_heavy_share_is_marked_in_the_line(floor: Floor) -> None:
    run_sale(floor, [("d-200", "9000.00"), ("d-300", "3000.00")])
    report = concentration.build(floor.conn, floor.code)
    assert "<--" in report.buyers[0].line()
    assert "<--" not in report.buyers[1].line()


def test_the_rendered_report_covers_both_sides(floor: Floor) -> None:
    run_sale(floor, [("d-200", "6000.00")])
    printed = "\n".join(concentration.render(floor.conn, floor.code))
    assert "buyers" in printed
    assert "sellers" in printed
    assert "concentrated" in printed


def test_nothing_here_refuses_anything(floor: Floor) -> None:
    """A concentrated sale is a fact, not a fault. The report only says so."""
    run_sale(floor, [("d-200", "9000.00")])
    report = concentration.build(floor.conn, floor.code)
    assert report.is_concentrated
    assert report.gross == Money.parse("9000.00")
