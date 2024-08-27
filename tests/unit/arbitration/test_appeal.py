from datetime import datetime, timedelta

import pytest

from autozest.arbitration.appeal import APPEAL_WINDOW, AppealService, AppealStatus
from autozest.arbitration.claims import ClaimStatus, Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import Conflict, NotFound, PolicyViolation, ValidationError
from autozest.money import Money
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)
DECIDED_AT = datetime(2024, 3, 14, 10, 0)


@pytest.fixture()
def appeals(floor: Floor) -> AppealService:
    return AppealService(floor.conn)


def decided(
    floor: Floor,
    resolution: Resolution = Resolution.REPAIR_ALLOWANCE,
    amount: str | None = "600.00",
) -> str:
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    ResolutionService(floor.conn).resolve(
        claim.id,
        resolution,
        amount=Money.parse(amount) if amount else None,
        reason="shop quote",
        at=DECIDED_AT,
    )
    return claim.id


def test_the_buyer_can_appeal(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(
        claim, "d-200", "the quote was for the wrong engine", at=DECIDED_AT + timedelta(hours=2)
    )
    assert appeal.status is AppealStatus.OPEN
    assert appeals.for_claim(claim) is not None


def test_the_seller_can_appeal_too(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(
        claim, "d-100", "we announced the noise", at=DECIDED_AT + timedelta(hours=2)
    )
    assert appeal.raised_by == "d-100"


def test_nobody_else_can(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    with pytest.raises(PolicyViolation) as caught:
        appeals.raise_appeal(claim, "d-300", "curious", at=DECIDED_AT + timedelta(hours=2))
    assert caught.value.rule == "appeal.not_a_party"


def test_an_appeal_needs_grounds(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    with pytest.raises(ValidationError, match="needs grounds"):
        appeals.raise_appeal(claim, "d-200", "   ")


def test_the_window_is_short(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    assert APPEAL_WINDOW.amount == 3
    with pytest.raises(PolicyViolation) as caught:
        appeals.raise_appeal(claim, "d-200", "late", at=DECIDED_AT + timedelta(days=20))
    assert caught.value.rule == "appeal.late"


def test_an_undecided_claim_cannot_be_appealed(floor: Floor, appeals: AppealService) -> None:
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    with pytest.raises(PolicyViolation) as caught:
        appeals.raise_appeal(claim.id, "d-200", "too soon")
    assert caught.value.rule == "appeal.not_decided"


def test_one_appeal_per_decision(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeals.raise_appeal(claim, "d-200", "first", at=DECIDED_AT + timedelta(hours=2))
    with pytest.raises(Conflict):
        appeals.raise_appeal(claim, "d-100", "second", at=DECIDED_AT + timedelta(hours=3))


def test_upholding_leaves_the_claim_alone(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    appeals.decide(appeal.id, AppealStatus.UPHELD, decided_by="m.byrne", note="stands")
    settled = appeals.claims.get(claim)
    assert settled.outcome == Money.parse("600.00")
    assert settled.status is ClaimStatus.RESOLVED


def test_varying_moves_the_figure(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    appeals.decide(
        appeal.id,
        AppealStatus.VARIED,
        decided_by="m.byrne",
        note="split the difference",
        new_outcome=Money.parse("900.00"),
    )
    assert appeals.claims.get(claim).outcome == Money.parse("900.00")


def test_a_variation_needs_a_figure(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    with pytest.raises(ValidationError, match="needs the new figure"):
        appeals.decide(appeal.id, AppealStatus.VARIED, decided_by="m.byrne", note="x")


def test_overturning_a_paid_claim_denies_it(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(
        claim, "d-100", "we announced it", at=DECIDED_AT + timedelta(hours=2)
    )
    appeals.decide(
        appeal.id, AppealStatus.OVERTURNED, decided_by="m.byrne", note="it was called"
    )
    settled = appeals.claims.get(claim)
    assert settled.status is ClaimStatus.DENIED
    assert settled.outcome == Money.zero()


def test_overturning_a_denial_pays_it(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor, Resolution.DENIED, None)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    appeals.decide(
        appeal.id,
        AppealStatus.OVERTURNED,
        decided_by="m.byrne",
        note="it was not called",
        new_outcome=Money.parse("500.00"),
    )
    settled = appeals.claims.get(claim)
    assert settled.status is ClaimStatus.RESOLVED
    assert settled.outcome == Money.parse("500.00")


def test_an_appeal_is_decided_once(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    appeals.decide(appeal.id, AppealStatus.UPHELD, decided_by="m.byrne", note="stands")
    with pytest.raises(PolicyViolation) as caught:
        appeals.decide(appeal.id, AppealStatus.VARIED, decided_by="m.byrne", note="again")
    assert caught.value.rule == "appeal.already_decided"


def test_a_decision_needs_a_note(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    with pytest.raises(ValidationError, match="needs a note"):
        appeals.decide(appeal.id, AppealStatus.UPHELD, decided_by="m.byrne", note=" ")


def test_the_open_queue_and_withdrawal(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    assert [a.id for a in appeals.open_appeals()] == [appeal.id]
    appeals.withdraw(appeal.id, "d-200", "settled it between ourselves")
    assert appeals.open_appeals() == []


def test_an_unknown_appeal(appeals: AppealService) -> None:
    with pytest.raises(NotFound):
        appeals.get("E-2024-999999")


def test_the_appeal_shows_on_the_claim_history(floor: Floor, appeals: AppealService) -> None:
    claim = decided(floor)
    appeal = appeals.raise_appeal(claim, "d-200", "grounds", at=DECIDED_AT + timedelta(hours=2))
    appeals.decide(appeal.id, AppealStatus.UPHELD, decided_by="m.byrne", note="stands")
    kinds = [event.kind for event in appeals.claims.events(claim)]
    assert "appealed" in kinds
    assert "appeal upheld" in kinds
