from datetime import date

import pytest

from autozest.arbitration.claims import (
    CLAIM_TRANSITIONS,
    Claim,
    ClaimRepository,
    ClaimStatus,
    Resolution,
)
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import NotFound, StateError, ValidationError
from autozest.money import Money
from tests.conftest import Floor


@pytest.fixture()
def sold_run(floor: Floor) -> int:
    run_id = floor.catalogue(floor_price="5000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    return run_id


@pytest.fixture()
def claims(floor: Floor) -> ClaimRepository:
    return ClaimRepository(floor.conn)


def claim(reference: str, run_id: int, **kwargs: object) -> Claim:
    defaults: dict[str, object] = {
        "id": reference,
        "run_id": run_id,
        "claimant_id": "d-200",
        "defect": DefectClass.ENGINE,
        "summary": "knocking on a cold start",
        "policy_version": "2024.1",
    }
    defaults.update(kwargs)
    return Claim(**defaults)  # type: ignore[arg-type]


def test_a_claim_needs_a_description(sold_run: int) -> None:
    with pytest.raises(ValidationError, match="description of the complaint"):
        claim("A-2024-000001", sold_run, summary="   ")


def test_a_resolved_claim_needs_a_resolution(sold_run: int) -> None:
    with pytest.raises(ValidationError, match="needs a resolution"):
        claim("A-2024-000001", sold_run, status=ClaimStatus.RESOLVED)


def test_the_first_reference_of_the_year(claims: ClaimRepository) -> None:
    assert claims.next_id(date(2024, 4, 2)) == "A-2024-000001"


def test_references_follow_the_highest_used(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000007", sold_run), "desk")
    assert claims.next_id(date(2024, 4, 2)) == "A-2024-000008"


def test_opening_a_claim_writes_its_first_event(claims: ClaimRepository, sold_run: int) -> None:
    opened = claims.add(claim("A-2024-000001", sold_run), "desk")
    assert opened.status is ClaimStatus.OPEN
    events = claims.events(opened.id)
    assert [event.kind for event in events] == ["opened"]
    assert events[0].actor == "desk"


def test_round_trip(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000001", sold_run, claimed=Money.parse("1800.00")), "desk")
    found = claims.get("A-2024-000001")
    assert found.defect is DefectClass.ENGINE
    assert found.claimed == Money.parse("1800.00")
    assert found.policy_version == "2024.1"
    assert found.opened_at is not None


def test_an_unknown_claim(claims: ClaimRepository) -> None:
    with pytest.raises(NotFound):
        claims.get("A-2024-999999")


def test_a_claim_moves_through_review(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    claims.move("A-2024-000001", ClaimStatus.UNDER_REVIEW, "desk", "chased the seller")
    moved = claims.get("A-2024-000001")
    assert moved.status is ClaimStatus.UNDER_REVIEW
    assert [event.kind for event in claims.events("A-2024-000001")] == [
        "opened",
        "under_review",
    ]
    assert claims.events("A-2024-000001")[1].detail == "chased the seller"


def test_a_claim_cannot_be_resolved_straight_from_open(
    claims: ClaimRepository, sold_run: int
) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    with pytest.raises(StateError):
        claims.move("A-2024-000001", ClaimStatus.RESOLVED, "desk")


def test_a_decided_claim_does_not_move_again(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    claims.move("A-2024-000001", ClaimStatus.DENIED, "desk")
    with pytest.raises(StateError, match="nothing"):
        claims.move("A-2024-000001", ClaimStatus.UNDER_REVIEW, "desk")


def test_the_finished_statuses() -> None:
    assert ClaimStatus.RESOLVED.is_finished
    assert ClaimStatus.WITHDRAWN.is_finished
    assert not ClaimStatus.UNDER_REVIEW.is_finished
    for status, allowed in CLAIM_TRANSITIONS.items():
        if status.is_finished:
            assert allowed == frozenset()


def test_a_withdrawn_claim_does_not_use_up_the_buyers_go(
    claims: ClaimRepository, sold_run: int
) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    claims.move("A-2024-000001", ClaimStatus.WITHDRAWN, "desk", "seller sorted it privately")
    assert claims.settled_classes(sold_run) == frozenset()


def test_a_denied_claim_does(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    claims.move("A-2024-000001", ClaimStatus.DENIED, "desk")
    assert claims.settled_classes(sold_run) == frozenset({DefectClass.ENGINE})


def test_listing_by_run_and_by_claimant(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    claims.add(claim("A-2024-000002", sold_run, defect=DefectClass.TITLE), "desk")
    assert len(claims.for_run(sold_run)) == 2
    assert len(claims.for_claimant("d-200")) == 2
    assert claims.for_claimant("d-999") == []


def test_the_desk_queue(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    claims.add(claim("A-2024-000002", sold_run, defect=DefectClass.TITLE), "desk")
    claims.move("A-2024-000002", ClaimStatus.DENIED, "desk")
    assert [c.id for c in claims.open_claims()] == ["A-2024-000001"]
    assert claims.count_open() == 1


def test_narrowing_a_claimants_list_by_status(claims: ClaimRepository, sold_run: int) -> None:
    claims.add(claim("A-2024-000001", sold_run), "desk")
    claims.move("A-2024-000001", ClaimStatus.UNDER_REVIEW, "desk")
    assert len(claims.for_claimant("d-200", ClaimStatus.UNDER_REVIEW)) == 1
    assert claims.for_claimant("d-200", ClaimStatus.OPEN) == []


def test_only_an_adjustment_restates_the_price_the_fees_used() -> None:
    assert Resolution.PRICE_ADJUSTMENT.changes_the_hammer
    assert Resolution.SPLIT.changes_the_hammer
    assert not Resolution.REPAIR_ALLOWANCE.changes_the_hammer
    assert not Resolution.BUY_BACK.changes_the_hammer


def test_a_buy_back_is_the_only_one_that_unwinds() -> None:
    assert Resolution.BUY_BACK.unwinds_the_sale
    for other in (Resolution.PRICE_ADJUSTMENT, Resolution.REPAIR_ALLOWANCE, Resolution.SPLIT):
        assert not other.unwinds_the_sale
        assert other.moves_money
    assert not Resolution.DENIED.moves_money
