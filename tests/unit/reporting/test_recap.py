from decimal import Decimal

import pytest

from autozest.block.bidding import BiddingService
from autozest.block.models import RunStatus
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.reporting import recap as recap_report
from tests.conftest import Floor


def sell(floor: Floor, run_id: int, buyer: str, amount: str) -> None:
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, buyer, Money.parse(amount))
    bidding.fall(run_id)


@pytest.fixture()
def run_sale(floor: Floor) -> Floor:
    floor.catalogue(floor_price="5000.00")
    floor.catalogue(floor_price="5000.00")
    floor.catalogue(floor_price="5000.00", light=Light.RED)
    floor.sales.open_sale(floor.code)
    return floor


def test_an_untouched_sale_reports_zeroes(floor: Floor) -> None:
    floor.catalogue()
    recap = recap_report.build(floor.conn, floor.code)
    assert recap.catalogued == 1
    assert recap.sold == 0
    assert recap.gross == Money.zero()
    assert recap.average == Money.zero()
    assert recap.sell_through == Decimal("0.0")


def test_a_sale_with_two_sold_and_one_short(run_sale: Floor) -> None:
    runs = run_sale.sales.runs(run_sale.code)
    sell(run_sale, runs[0].id or 0, "d-200", "5200.00")
    sell(run_sale, runs[1].id or 0, "d-200", "8100.00")
    sell(run_sale, runs[2].id or 0, "d-200", "4200.00")
    recap = recap_report.build(run_sale.conn, run_sale.code)
    assert recap.sold == 2
    assert recap.on_if == 1
    assert recap.gross == Money.parse("13300.00")
    assert recap.average == Money.parse("6650.00")
    assert recap.sell_through == Decimal("66.7")
    assert not recap.is_final


def test_a_pulled_unit_does_not_drag_the_rate_down(run_sale: Floor) -> None:
    runs = run_sale.sales.runs(run_sale.code)
    sell(run_sale, runs[0].id or 0, "d-200", "5200.00")
    run_sale.conn.execute(
        "UPDATE run SET status = ? WHERE id = ?", (RunStatus.PULLED.value, runs[1].id)
    )
    run_sale.conn.execute(
        "UPDATE run SET status = ? WHERE id = ?", (RunStatus.PULLED.value, runs[2].id)
    )
    recap = recap_report.build(run_sale.conn, run_sale.code)
    assert recap.ran == 1
    assert recap.pulled == 2
    assert recap.sell_through == Decimal("100.0")


def test_the_lights_are_counted(run_sale: Floor) -> None:
    recap = recap_report.build(run_sale.conn, run_sale.code)
    assert recap.by_light[Light.GREEN] == 2
    assert recap.by_light[Light.RED] == 1
    assert recap.by_light[Light.BLUE] == 0


def test_the_lanes_are_broken_out(run_sale: Floor) -> None:
    runs = run_sale.sales.runs(run_sale.code)
    sell(run_sale, runs[0].id or 0, "d-200", "5200.00")
    recap = recap_report.build(run_sale.conn, run_sale.code)
    lane_a = next(lane for lane in recap.lanes if lane.letter == "A")
    lane_b = next(lane for lane in recap.lanes if lane.letter == "B")
    assert lane_a.sold == 1
    assert lane_a.gross == Money.parse("5200.00")
    assert lane_b.ran == 0
    assert lane_b.sell_through == Decimal("0.0")


def test_the_top_of_the_night_is_sorted(run_sale: Floor) -> None:
    runs = run_sale.sales.runs(run_sale.code)
    sell(run_sale, runs[0].id or 0, "d-200", "5200.00")
    sell(run_sale, runs[1].id or 0, "d-200", "8100.00")
    recap = recap_report.build(run_sale.conn, run_sale.code)
    assert [str(amount) for _, amount in recap.top] == ["8,100.00", "5,200.00"]


def test_a_finished_sale_says_it_is_final(run_sale: Floor) -> None:
    runs = run_sale.sales.runs(run_sale.code)
    for run in runs:
        sell(run_sale, run.id or 0, "d-200", "5200.00")
    recap = recap_report.build(run_sale.conn, run_sale.code)
    assert recap.is_final
    assert recap.sold == 3


def test_the_rendered_recap_carries_the_numbers(run_sale: Floor) -> None:
    runs = run_sale.sales.runs(run_sale.code)
    sell(run_sale, runs[0].id or 0, "d-200", "5200.00")
    text = "\n".join(recap_report.render(run_sale.conn, run_sale.code))
    assert "sale recap" in text
    assert "5,200.00" in text
    assert "sell through" in text
    assert "top of the night" in text
    assert "A001" in text


def test_a_recap_with_nothing_sold_leaves_out_the_top(floor: Floor) -> None:
    floor.catalogue()
    text = "\n".join(recap_report.render(floor.conn, floor.code))
    assert "top of the night" not in text
