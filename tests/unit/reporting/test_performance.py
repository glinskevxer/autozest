from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from autozest.arbitration.claims import Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.money import Money
from autozest.reporting.performance import PerformanceService
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def performance(floor: Floor) -> PerformanceService:
    return PerformanceService(floor.conn)


def catalogue(floor: Floor, floor_price: str = "3000.00") -> int:
    return floor.catalogue(floor_price=floor_price)


def put_through(
    floor: Floor, *, price: str | None, run_id: int | None = None, floor_price: str = "3000.00"
) -> int:
    """Run one unit across the block. Catalogues it first if it is not in yet."""
    if run_id is None:
        run_id = catalogue(floor, floor_price)
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    if price is not None:
        bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    return run_id


def test_a_sale_with_nothing_in_it(performance: PerformanceService) -> None:
    assert performance.sellers() == []
    assert performance.buyers() == []


def test_sell_through_and_gross(floor: Floor, performance: PerformanceService) -> None:
    first, second = catalogue(floor), catalogue(floor)
    put_through(floor, price="8200.00", run_id=first)
    put_through(floor, price=None, run_id=second)
    record = performance.sellers(floor.code)[0]
    assert record.consigned == 2
    assert record.ran == 2
    assert record.sold == 1
    assert record.gross == Money.parse("8200.00")
    assert record.sell_through == Decimal("50.0")
    assert record.average == Money.parse("8200.00")


def test_the_gap_to_the_floor_is_what_the_room_added(
    floor: Floor, performance: PerformanceService
) -> None:
    put_through(floor, price="8200.00", floor_price="5000.00")
    assert performance.sellers(floor.code)[0].floor_gap == Money.parse("3200.00")


def test_a_unit_that_sold_under_its_floor_shows_a_negative_gap(
    floor: Floor, performance: PerformanceService
) -> None:
    run_id = put_through(floor, price="4200.00", floor_price="5000.00")
    floor.conn.execute("UPDATE run SET status = 'sold' WHERE id = ?", (run_id,))
    assert performance.sellers(floor.code)[0].floor_gap.is_negative


def test_the_arbitration_rate_sits_beside_the_sell_through(
    floor: Floor, performance: PerformanceService
) -> None:
    run_id = put_through(floor, price="8200.00")
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    ResolutionService(floor.conn).resolve(
        claim.id, Resolution.REPAIR_ALLOWANCE, amount=Money.parse("400.00"), reason="quote"
    )
    record = performance.sellers(floor.code)[0]
    assert record.claims == 1
    assert record.upheld == 1
    assert record.arbitration_rate == Decimal("100.0")
    assert record.upheld_rate == Decimal("100.0")
    assert record.is_optimistic


def test_a_clean_seller_is_not_flagged(floor: Floor, performance: PerformanceService) -> None:
    put_through(floor, price="8200.00")
    assert not performance.sellers(floor.code)[0].is_optimistic


def test_a_seller_who_put_nothing_through(performance: PerformanceService) -> None:
    record = performance.seller("d-999")
    assert record.consigned == 0
    assert record.sell_through == Decimal("0.0")
    assert record.arbitration_rate == Decimal("0.0")
    assert record.average == Money.zero()


def test_buyers_are_ranked_by_spend(floor: Floor, performance: PerformanceService) -> None:
    first, second = catalogue(floor), catalogue(floor)
    put_through(floor, price="8200.00", run_id=first)
    put_through(floor, price="4200.00", run_id=second)
    records = performance.buyers(floor.code)
    assert len(records) == 1
    assert records[0].bought == 2
    assert records[0].spend == Money.parse("12400.00")
    assert records[0].average == Money.parse("6200.00")
    assert records[0].claim_rate == Decimal("0.0")


def test_everything_across_every_sale(floor: Floor, performance: PerformanceService) -> None:
    put_through(floor, price="8200.00")
    assert performance.sellers()[0].sold == 1
    assert performance.buyers()[0].bought == 1
