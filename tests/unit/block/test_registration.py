import pytest

from autozest.block.registration import (
    BLOCKING_BALANCE,
    RegistrationService,
    RegistrationStatus,
)
from autozest.catalog.accounts import AccountRepository, AccountStatus
from autozest.errors import Conflict, NotFound, PolicyViolation
from autozest.money import Money
from autozest.settlement.floorplan import FloorplanService
from autozest.settlement.postings import PostingService
from tests.conftest import Floor


@pytest.fixture()
def desk(floor: Floor) -> RegistrationService:
    return RegistrationService(floor.conn)


def owe_money(floor: Floor) -> None:
    from autozest.block.bidding import BiddingService

    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    PostingService(floor.conn).post_sale(run_id)


def test_a_clean_buyer_registers(floor: Floor, desk: RegistrationService) -> None:
    registration = desk.register(floor.code, "d-200")
    assert registration.status is RegistrationStatus.ACTIVE
    assert registration.badge == 1
    assert desk.blockers("d-200") == []


def test_badges_are_unique_within_a_sale(floor: Floor, desk: RegistrationService) -> None:
    first = desk.register(floor.code, "d-200")
    second = desk.register(floor.code, "d-300")
    assert first.badge != second.badge
    assert [r.badge for r in desk.for_sale(floor.code)] == [1, 2]


def test_a_buyer_keeps_their_badge_between_sales(
    floor: Floor, desk: RegistrationService
) -> None:
    desk.register(floor.code, "d-300", badge=40)
    from datetime import date

    other = floor.sales.create(date(2024, 3, 19), "Next Tuesday", {"A": 10})
    assert desk.register(other.code, "d-300").badge == 40


def test_a_taken_badge_falls_back_to_the_lowest_free_one(
    floor: Floor, desk: RegistrationService
) -> None:
    desk.register(floor.code, "d-200", badge=40)
    from datetime import date

    other = floor.sales.create(date(2024, 3, 19), "Next Tuesday", {"A": 10})
    desk.register(other.code, "d-300", badge=40)
    assert desk.register(other.code, "d-200").badge == 1


def test_registering_twice_for_the_same_sale(floor: Floor, desk: RegistrationService) -> None:
    desk.register(floor.code, "d-200")
    with pytest.raises(Conflict):
        desk.register(floor.code, "d-200")


def test_a_suspended_account_is_blocked(floor: Floor, desk: RegistrationService) -> None:
    AccountRepository(floor.conn).set_status("d-200", AccountStatus.SUSPENDED)
    assert "the account is suspended" in desk.blockers("d-200")
    with pytest.raises(PolicyViolation) as caught:
        desk.register(floor.code, "d-200")
    assert caught.value.rule == "registration.blocked"


def test_a_finance_account_does_not_bid(floor: Floor, desk: RegistrationService) -> None:
    assert any("does not bid" in reason for reason in desk.blockers("f-1"))


def test_an_outstanding_balance_blocks(floor: Floor, desk: RegistrationService) -> None:
    owe_money(floor)
    reasons = desk.blockers("d-200")
    assert any("outstanding on the account" in reason for reason in reasons)
    assert Money.parse("500.00") == BLOCKING_BALANCE


def test_an_exhausted_line_blocks(floor: Floor, desk: RegistrationService) -> None:
    credit = FloorplanService(floor.conn)
    credit.open_line("L-1", "d-200", "Ninth Street", Money.parse("5000.00"))
    credit.draw("L-1", Money.parse("5000.00"))
    assert "the floor plan line is used up" in desk.blockers("d-200")


def test_the_desk_can_register_anyway_and_the_badge_says_so(
    floor: Floor, desk: RegistrationService
) -> None:
    AccountRepository(floor.conn).set_status("d-200", AccountStatus.SUSPENDED)
    registration = desk.register(floor.code, "d-200", force=True)
    assert registration.status is RegistrationStatus.BLOCKED
    assert registration.blocked_reason is not None


def test_bidding_needs_a_registration(floor: Floor, desk: RegistrationService) -> None:
    with pytest.raises(PolicyViolation) as caught:
        desk.require_registered(floor.code, "d-200")
    assert caught.value.rule == "registration.missing"


def test_a_blocked_badge_does_not_bid(floor: Floor, desk: RegistrationService) -> None:
    AccountRepository(floor.conn).set_status("d-200", AccountStatus.SUSPENDED)
    desk.register(floor.code, "d-200", force=True)
    with pytest.raises(PolicyViolation) as caught:
        desk.require_registered(floor.code, "d-200")
    assert caught.value.rule == "registration.not_active"


def test_a_registration_can_be_unblocked(floor: Floor, desk: RegistrationService) -> None:
    AccountRepository(floor.conn).set_status("d-200", AccountStatus.SUSPENDED)
    registration = desk.register(floor.code, "d-200", force=True)
    desk.set_status(registration.id, RegistrationStatus.ACTIVE)
    assert desk.require_registered(floor.code, "d-200").status is RegistrationStatus.ACTIVE


def test_looking_a_badge_up(floor: Floor, desk: RegistrationService) -> None:
    desk.register(floor.code, "d-200", badge=40)
    assert desk.by_badge(floor.code, 40).buyer_id == "d-200"
    with pytest.raises(NotFound):
        desk.by_badge(floor.code, 41)
