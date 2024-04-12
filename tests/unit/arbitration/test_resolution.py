from datetime import datetime, timedelta

import pytest

from autozest.arbitration.claims import ClaimStatus, Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import PolicyViolation, ValidationError
from autozest.money import Money
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def desk(floor: Floor) -> ArbitrationDesk:
    return ArbitrationDesk(floor.conn)


@pytest.fixture()
def resolving(floor: Floor) -> ResolutionService:
    return ResolutionService(floor.conn)


def sold(floor: Floor, price: str = "8200.00") -> int:
    run_id = floor.catalogue(floor_price="100.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    return run_id


def claimed(
    desk: ArbitrationDesk, run_id: int, defect: DefectClass = DefectClass.ENGINE
) -> str:
    claim = desk.open_claim(
        run_id, "d-200", defect, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    return claim.id


def test_a_repair_allowance_pays_the_buyer_and_leaves_the_price(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    outcome = resolving.resolve(
        reference,
        Resolution.REPAIR_ALLOWANCE,
        amount=Money.parse("1200.00"),
        reason="shop quote for a head gasket",
    )
    assert outcome.buyer_receives == Money.parse("1200.00")
    assert outcome.seller_pays == Money.parse("1200.00")
    assert outcome.hammer_after == Money.parse("8200.00")
    assert resolving.effective_hammer(run_id) == Money.parse("8200.00")


def test_a_price_adjustment_restates_the_hammer(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    outcome = resolving.resolve(
        reference,
        Resolution.PRICE_ADJUSTMENT,
        amount=Money.parse("1200.00"),
        reason="agreed with both sides",
    )
    assert outcome.hammer_after == Money.parse("7000.00")
    assert resolving.effective_hammer(run_id) == Money.parse("7000.00")
    assert resolving.hammer_for(run_id) == Money.parse("8200.00")


def test_a_split_is_shared_and_still_moves_the_hammer(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    outcome = resolving.resolve(
        reference, Resolution.SPLIT, amount=Money.parse("1000.00"), reason="met in the middle"
    )
    assert outcome.auction_pays == Money.parse("500.00")
    assert outcome.seller_pays == Money.parse("500.00")
    assert outcome.hammer_after == Money.parse("7200.00")


def test_a_buy_back_returns_the_whole_price(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    outcome = resolving.resolve(reference, Resolution.BUY_BACK, reason="frame was never called")
    assert outcome.unwinds
    assert outcome.buyer_receives == Money.parse("8200.00")
    assert resolving.was_bought_back(run_id)


def test_a_denial_moves_no_money(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    outcome = resolving.resolve(reference, Resolution.DENIED, reason="within tolerance")
    assert outcome.amount == Money.zero()
    assert desk.claims.get(reference).status is ClaimStatus.DENIED
    assert resolving.exposure(run_id) == Money.zero()


def test_an_arbitration_cannot_pay_more_than_the_unit_brought(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor, "4000.00")
    reference = claimed(desk, run_id)
    with pytest.raises(PolicyViolation) as caught:
        resolving.plan(reference, Resolution.REPAIR_ALLOWANCE, Money.parse("5000.00"))
    assert caught.value.rule == "arb.over_hammer"


def test_a_money_resolution_needs_an_amount(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    with pytest.raises(ValidationError, match="needs an amount"):
        resolving.plan(reference, Resolution.PRICE_ADJUSTMENT)
    with pytest.raises(ValidationError, match="negative"):
        resolving.plan(reference, Resolution.PRICE_ADJUSTMENT, Money(-1))


def test_a_decision_needs_a_reason(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    with pytest.raises(ValidationError, match="needs a reason"):
        resolving.resolve(reference, Resolution.DENIED, reason="  ")


def test_a_claim_is_decided_only_once_the_desk_has_taken_it_up(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    with pytest.raises(PolicyViolation) as caught:
        resolving.resolve(claim.id, Resolution.DENIED, reason="no")
    assert caught.value.rule == "arb.not_under_review"


def test_a_second_adjustment_measures_against_what_is_left(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    first = claimed(desk, run_id)
    resolving.resolve(
        first, Resolution.PRICE_ADJUSTMENT, amount=Money.parse("6000.00"), reason="agreed"
    )
    second = claimed(desk, run_id, DefectClass.STRUCTURAL)
    with pytest.raises(PolicyViolation) as caught:
        resolving.plan(second, Resolution.PRICE_ADJUSTMENT, Money.parse("3000.00"))
    assert caught.value.rule == "arb.over_hammer"
    assert "2,200.00" in str(caught.value)


def test_an_allowance_does_not_reduce_what_a_later_adjustment_can_take(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    first = claimed(desk, run_id)
    resolving.resolve(
        first, Resolution.REPAIR_ALLOWANCE, amount=Money.parse("6000.00"), reason="quote"
    )
    assert resolving.effective_hammer(run_id) == Money.parse("8200.00")
    second = claimed(desk, run_id, DefectClass.STRUCTURAL)
    outcome = resolving.plan(second, Resolution.PRICE_ADJUSTMENT, Money.parse("3000.00"))
    assert outcome.hammer_after == Money.parse("5200.00")


def test_exposure_adds_up_every_resolved_claim(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    first = claimed(desk, run_id)
    resolving.resolve(
        first, Resolution.REPAIR_ALLOWANCE, amount=Money.parse("400.00"), reason="quote"
    )
    second = claimed(desk, run_id, DefectClass.STRUCTURAL)
    resolving.resolve(
        second, Resolution.PRICE_ADJUSTMENT, amount=Money.parse("600.00"), reason="agreed"
    )
    assert resolving.exposure(run_id) == Money.parse("1000.00")


def test_a_buy_back_after_an_adjustment_returns_the_adjusted_price(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    first = claimed(desk, run_id)
    resolving.resolve(
        first, Resolution.PRICE_ADJUSTMENT, amount=Money.parse("1200.00"), reason="agreed"
    )
    second = claimed(desk, run_id, DefectClass.STRUCTURAL)
    outcome = resolving.resolve(second, Resolution.BUY_BACK, reason="frame was never called")
    assert outcome.buyer_receives == Money.parse("7000.00")


def test_the_decision_is_on_the_claim_record(
    floor: Floor, desk: ArbitrationDesk, resolving: ResolutionService
) -> None:
    run_id = sold(floor)
    reference = claimed(desk, run_id)
    resolving.resolve(
        reference,
        Resolution.REPAIR_ALLOWANCE,
        amount=Money.parse("400.00"),
        reason="shop quote",
        actor="p.dunne",
    )
    claim = desk.claims.get(reference)
    assert claim.status is ClaimStatus.RESOLVED
    assert claim.resolution is Resolution.REPAIR_ALLOWANCE
    assert claim.outcome == Money.parse("400.00")
    assert claim.reason == "shop quote"
    assert claim.decided_at is not None
    assert [event.actor for event in desk.claims.events(reference)][-1] == "p.dunne"
