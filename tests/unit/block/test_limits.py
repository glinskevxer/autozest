import pytest

from autozest.block.bidding import BiddingService
from autozest.block.limits import LimitService, Scope
from autozest.errors import NotFound, PolicyViolation, ValidationError
from autozest.money import Money
from tests.conftest import Floor


@pytest.fixture()
def limits(floor: Floor) -> LimitService:
    return LimitService(floor.conn)


def test_a_buyer_with_no_limit_is_not_checked(floor: Floor, limits: LimitService) -> None:
    limits.check("d-200", floor.code, Money.parse("90000.00"))


def test_setting_and_describing_a_limit(limits: LimitService) -> None:
    limit = limits.set_limit(
        "d-200",
        Scope.PER_UNIT,
        Money.parse("4000.00"),
        reason="asked us to hold them to it",
        set_by="p.dunne",
    )
    assert limit.is_live
    assert "4,000.00 per_unit, asked for" in limit.describe()


def test_an_imposed_limit_says_so(limits: LimitService) -> None:
    limit = limits.set_limit(
        "d-200",
        Scope.PER_SALE,
        Money.parse("20000.00"),
        reason="two late payments",
        set_by="m.byrne",
        imposed=True,
    )
    assert "imposed: two late payments" in limit.describe()


def test_a_negative_limit_and_one_with_no_reason(limits: LimitService) -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        limits.set_limit("d-200", Scope.PER_UNIT, Money(-1), reason="x", set_by="p")
    with pytest.raises(ValidationError, match="needs a reason"):
        limits.set_limit("d-200", Scope.PER_UNIT, Money.zero(), reason=" ", set_by="p")


def test_an_unknown_limit(limits: LimitService) -> None:
    with pytest.raises(NotFound):
        limits.get("L-2024-999999")


def test_setting_a_second_one_lifts_the_first(limits: LimitService) -> None:
    first = limits.set_limit(
        "d-200", Scope.PER_UNIT, Money.parse("4000.00"), reason="a", set_by="p"
    )
    limits.set_limit("d-200", Scope.PER_UNIT, Money.parse("6000.00"), reason="b", set_by="p")
    assert not limits.get(first.id).is_live
    live = limits.live("d-200", Scope.PER_UNIT)
    assert live is not None and live.amount == Money.parse("6000.00")


def test_a_per_unit_cap_stops_one_big_bid(floor: Floor, limits: LimitService) -> None:
    limits.set_limit(
        "d-200", Scope.PER_UNIT, Money.parse("4000.00"), reason="asked", set_by="p"
    )
    limits.check("d-200", floor.code, Money.parse("4000.00"))
    with pytest.raises(PolicyViolation) as caught:
        limits.check("d-200", floor.code, Money.parse("4001.00"))
    assert caught.value.rule == "limit.exceeded"
    assert caught.value.context["scope"] == "per_unit"


def test_a_per_sale_cap_counts_what_is_already_bought(
    floor: Floor, limits: LimitService
) -> None:
    first = floor.catalogue(floor_price="3000.00")
    second = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(first, "d-200", Money.parse("8200.00"))
    bidding.fall(first)
    limits.set_limit(
        "d-200", Scope.PER_SALE, Money.parse("10000.00"), reason="asked", set_by="p"
    )
    with pytest.raises(PolicyViolation) as caught:
        bidding.bid(second, "d-200", Money.parse("2000.00"))
    assert caught.value.rule == "limit.exceeded"
    assert caught.value.context["already"] == "8,200.00"


def test_a_bid_that_fits_under_the_sale_cap_is_taken(
    floor: Floor, limits: LimitService
) -> None:
    first = floor.catalogue(floor_price="3000.00")
    second = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(first, "d-200", Money.parse("8200.00"))
    bidding.fall(first)
    limits.set_limit(
        "d-200", Scope.PER_SALE, Money.parse("20000.00"), reason="asked", set_by="p"
    )
    bidding.bid(second, "d-200", Money.parse("8000.00"))
    assert bidding.fall(second).buyer_id == "d-200"


def test_lifting_a_limit_lets_the_bid_through(floor: Floor, limits: LimitService) -> None:
    limit = limits.set_limit(
        "d-200", Scope.PER_UNIT, Money.parse("100.00"), reason="asked", set_by="p"
    )
    with pytest.raises(PolicyViolation):
        limits.check("d-200", floor.code, Money.parse("8000.00"))
    limits.lift(limit.id)
    limits.check("d-200", floor.code, Money.parse("8000.00"))


def test_the_scopes_that_count_spending() -> None:
    assert not Scope.PER_UNIT.counts_spending
    assert Scope.PER_SALE.counts_spending
    assert Scope.PER_DAY.counts_spending


def test_all_live_limits_come_back_together(limits: LimitService) -> None:
    limits.set_limit("d-200", Scope.PER_UNIT, Money.parse("4000.00"), reason="a", set_by="p")
    limits.set_limit("d-200", Scope.PER_SALE, Money.parse("20000.00"), reason="b", set_by="p")
    assert len(limits.all_live("d-200")) == 2
