import pytest

from autozest.block.bidding import BiddingService
from autozest.errors import PolicyViolation, ValidationError
from autozest.money import Money
from autozest.settlement.adjustments import (
    APPROVAL_THRESHOLD,
    AdjustmentKind,
    AdjustmentService,
)
from autozest.settlement.ledger import Account, Ledger
from autozest.settlement.postings import PostingService
from autozest.settlement.statements import Side, StatementService
from tests.conftest import Floor


@pytest.fixture()
def adjustments(floor: Floor) -> AdjustmentService:
    return AdjustmentService(floor.conn)


def owing(floor: Floor) -> None:
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    PostingService(floor.conn).post_sale(run_id)


def test_a_small_credit_note_needs_one_name(
    floor: Floor, adjustments: AdjustmentService
) -> None:
    owing(floor)
    note = adjustments.credit_note(
        "d-200", Money.parse("100.00"), reason="fee charged twice", raised_by="p.dunne"
    )
    assert note.kind is AdjustmentKind.CREDIT_NOTE
    assert not note.needed_approval
    assert StatementService(floor.conn).invoice("d-200").balance == Money.parse("8445.00")


def test_anything_over_the_threshold_needs_a_second(
    floor: Floor, adjustments: AdjustmentService
) -> None:
    owing(floor)
    with pytest.raises(PolicyViolation) as caught:
        adjustments.credit_note(
            "d-200", APPROVAL_THRESHOLD + Money(1), reason="goodwill", raised_by="p.dunne"
        )
    assert caught.value.rule == "adjustment.needs_approval"


def test_the_second_name_has_to_be_a_different_person(
    floor: Floor, adjustments: AdjustmentService
) -> None:
    owing(floor)
    with pytest.raises(PolicyViolation) as caught:
        adjustments.credit_note(
            "d-200",
            Money.parse("400.00"),
            reason="goodwill",
            raised_by="p.dunne",
            approved_by="p.dunne",
        )
    assert caught.value.rule == "adjustment.same_person"


def test_an_approved_credit_note_goes_through(
    floor: Floor, adjustments: AdjustmentService
) -> None:
    owing(floor)
    note = adjustments.credit_note(
        "d-200",
        Money.parse("400.00"),
        reason="six weeks to title",
        raised_by="p.dunne",
        approved_by="m.byrne",
    )
    assert note.needed_approval
    assert note.approved_by == "m.byrne"


def test_an_adjustment_of_nothing(floor: Floor, adjustments: AdjustmentService) -> None:
    with pytest.raises(ValidationError, match="for something"):
        adjustments.credit_note("d-200", Money.zero(), reason="x", raised_by="p.dunne")


def test_an_adjustment_needs_a_reason(floor: Floor, adjustments: AdjustmentService) -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        adjustments.credit_note("d-200", Money.parse("10.00"), reason="  ", raised_by="p")


def test_a_write_off_lands_on_its_own_account(
    floor: Floor, adjustments: AdjustmentService
) -> None:
    owing(floor)
    adjustments.write_off(
        "d-200", Money.parse("200.00"), reason="stopped answering", raised_by="p.dunne"
    )
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.WRITE_OFF) == Money.parse("200.00")
    assert ledger.balance(Account.FEE_INCOME) == Money.parse("580.00")
    assert ledger.is_balanced()


def test_a_credit_note_lands_against_income_instead(
    floor: Floor, adjustments: AdjustmentService
) -> None:
    owing(floor)
    adjustments.credit_note(
        "d-200", Money.parse("200.00"), reason="fee charged twice", raised_by="p.dunne"
    )
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.WRITE_OFF) == Money.zero()
    assert ledger.balance(Account.FEE_INCOME) == Money.parse("380.00")


def test_a_write_off_cannot_be_more_than_the_debt(
    floor: Floor, adjustments: AdjustmentService
) -> None:
    owing(floor)
    with pytest.raises(PolicyViolation) as caught:
        adjustments.write_off(
            "d-200",
            Money.parse("9000.00"),
            reason="gone",
            raised_by="p.dunne",
            approved_by="m.byrne",
        )
    assert caught.value.rule == "adjustment.over_balance"


def test_a_seller_side_credit_note(floor: Floor, adjustments: AdjustmentService) -> None:
    owing(floor)
    adjustments.credit_note(
        "d-100",
        Money.parse("50.00"),
        reason="detail never done",
        raised_by="p.dunne",
        side=Side.SELLER,
    )
    assert StatementService(floor.conn).seller_statement("d-100").balance == Money.parse(
        "8015.00"
    )


def test_the_history_and_the_total(floor: Floor, adjustments: AdjustmentService) -> None:
    owing(floor)
    adjustments.credit_note(
        "d-200", Money.parse("100.00"), reason="fee twice", raised_by="p.dunne"
    )
    adjustments.write_off("d-200", Money.parse("200.00"), reason="gone", raised_by="p.dunne")
    history = adjustments.history("d-200")
    assert len(history) == 2
    assert adjustments.written_off() == Money.parse("200.00")


def test_which_kind_was_agreed_to() -> None:
    assert AdjustmentKind.CREDIT_NOTE.is_agreed
    assert not AdjustmentKind.WRITE_OFF.is_agreed
