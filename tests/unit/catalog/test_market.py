from datetime import date, timedelta

import pytest

from autozest.block.bidding import BiddingService
from autozest.catalog.market import ENOUGH_COMPARABLES, MarketService
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.errors import ValidationError
from autozest.inspection.grade import GradeBand
from autozest.money import Money
from tests.conftest import SALE_DAY, VINS, Floor


@pytest.fixture()
def market(floor: Floor) -> MarketService:
    return MarketService(floor.conn)


def unit(floor: Floor) -> Vehicle:
    return VehicleRepository(floor.conn).get(VINS[0])


def sell_one(floor: Floor, price: str) -> None:
    sell_many(floor, [price])


def sell_many(floor: Floor, prices: list[str]) -> None:
    """Catalogue them all first, because opening freezes the run order."""
    run_ids = [floor.catalogue(floor_price="3000.00") for _ in prices]
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    for run_id, price in zip(run_ids, prices, strict=True):
        bidding.bid(run_id, "d-200", Money.parse(price))
        bidding.fall(run_id)


def test_nothing_to_go_on(floor: Floor, market: MarketService) -> None:
    advice = market.advise(unit(floor), GradeBand.CLEAN)
    assert advice.samples == 0
    assert advice.value == Money.zero()
    assert "nothing to go on" in advice.explain()


def test_a_guide_value_is_used_when_there_are_no_comparables(
    floor: Floor, market: MarketService
) -> None:
    vehicle = unit(floor)
    market.add_guide(
        vehicle.make, vehicle.model, vehicle.year, GradeBand.CLEAN, Money.parse("9000.00")
    )
    advice = market.advise(vehicle, GradeBand.CLEAN)
    assert advice.value == Money.parse("9000.00")
    assert advice.source == "guide"
    assert not advice.is_from_our_lanes


def test_a_negative_guide_value(floor: Floor, market: MarketService) -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        market.add_guide("Make", "Model0", 2019, GradeBand.CLEAN, Money(-1))


def test_the_advised_floor_leaves_the_room_somewhere_to_go(
    floor: Floor, market: MarketService
) -> None:
    vehicle = unit(floor)
    market.add_guide(
        vehicle.make, vehicle.model, vehicle.year, GradeBand.CLEAN, Money.parse("10000.00")
    )
    advice = market.advise(vehicle, GradeBand.CLEAN)
    assert advice.suggested_floor == Money.parse("8800.00")
    assert advice.suggested_floor < advice.value


def test_our_own_lanes_beat_a_guide_once_there_are_enough(
    floor: Floor, market: MarketService
) -> None:
    vehicle = unit(floor)
    market.add_guide(
        vehicle.make, vehicle.model, vehicle.year, GradeBand.CLEAN, Money.parse("20000.00")
    )
    # The fixture gives each unit its own model. Make the first three the same
    # thing so they are comparable to each other, which is the point.
    floor.conn.execute(
        "UPDATE vehicle SET make = ?, model = ?, model_year = ? WHERE vin IN (?, ?, ?)",
        (vehicle.make, vehicle.model, vehicle.year, *VINS[:3]),
    )
    sell_many(floor, ["8000.00", "8400.00", "9000.00"])
    advice = market.advise(vehicle, GradeBand.CLEAN, as_at=SALE_DAY)
    assert advice.is_from_our_lanes
    assert advice.samples >= ENOUGH_COMPARABLES
    assert "our lanes" in advice.explain()


def test_one_comparable_is_not_enough_on_its_own(floor: Floor, market: MarketService) -> None:
    vehicle = unit(floor)
    market.add_guide(
        vehicle.make, vehicle.model, vehicle.year, GradeBand.CLEAN, Money.parse("20000.00")
    )
    sell_one(floor, "8000.00")
    advice = market.advise(vehicle, GradeBand.CLEAN, as_at=SALE_DAY)
    assert advice.source == "guide"


def test_a_stale_sale_does_not_count(floor: Floor, market: MarketService) -> None:
    sell_one(floor, "8000.00")
    later = SALE_DAY + timedelta(days=400)
    assert market.comparables(unit(floor), as_at=later) == []


def test_the_latest_guide_value_wins(floor: Floor, market: MarketService) -> None:
    vehicle = unit(floor)
    market.add_guide(
        vehicle.make,
        vehicle.model,
        vehicle.year,
        GradeBand.CLEAN,
        Money.parse("9000.00"),
        as_at=date(2024, 1, 1),
    )
    market.add_guide(
        vehicle.make,
        vehicle.model,
        vehicle.year,
        GradeBand.CLEAN,
        Money.parse("8500.00"),
        as_at=date(2024, 3, 1),
    )
    assert market.guide(vehicle.make, vehicle.model, vehicle.year, GradeBand.CLEAN) == (
        Money.parse("8500.00")
    )


def test_a_guide_for_another_band_is_not_used(floor: Floor, market: MarketService) -> None:
    vehicle = unit(floor)
    market.add_guide(
        vehicle.make, vehicle.model, vehicle.year, GradeBand.ROUGH, Money.parse("4000.00")
    )
    assert market.guide(vehicle.make, vehicle.model, vehicle.year, GradeBand.CLEAN) is None
