import sqlite3
from datetime import datetime

import pytest

from autozest.block.bidding import BiddingService
from autozest.errors import Conflict, PolicyViolation, ValidationError
from autozest.money import Money
from autozest.settlement.ledger import Account, Ledger
from autozest.settlement.payments import BUYER_TERMS, Method, PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.statements import Side, StatementService
from autozest.storage.db import table_names
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def payments(floor: Floor) -> PaymentService:
    return PaymentService(floor.conn)


def posted(floor: Floor, price: str = "8200.00") -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    PostingService(floor.conn).post_sale(run_id)
    return run_id


def test_the_payment_table_is_there(db: sqlite3.Connection) -> None:
    assert "payment" in table_names(db)


def test_taking_money_in_clears_the_receivable(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    payments.receive("d-200", Money.parse("8545.00"), reference="wire 40182")
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.zero()
    assert ledger.balance(Account.CASH) == Money.parse("8545.00")
    assert ledger.is_balanced()


def test_a_part_payment_leaves_the_rest(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    payments.receive("d-200", Money.parse("5000.00"))
    assert StatementService(floor.conn).invoice("d-200").balance == Money.parse("3545.00")
    assert not payments.has_paid("d-200")


def test_paying_in_full_marks_the_buyer_paid(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    payments.receive("d-200", Money.parse("8545.00"))
    assert payments.has_paid("d-200")


def test_a_payment_of_nothing(payments: PaymentService) -> None:
    with pytest.raises(ValidationError, match="for something"):
        payments.receive("d-200", Money.zero())
    with pytest.raises(ValidationError):
        payments.receive("d-200", Money(-100))


def test_the_payment_record_keeps_the_cheque_number(
    floor: Floor, payments: PaymentService
) -> None:
    posted(floor)
    payment = payments.receive(
        "d-200",
        Money.parse("8545.00"),
        method=Method.CHEQUE,
        reference="40182",
        at=SOLD_AT,
        note="dropped at the office",
    )
    assert payment.reference == "40182"
    assert payment.method is Method.CHEQUE
    assert payment.side is Side.BUYER
    assert payment.received_at == SOLD_AT
    assert payments.get(payment.id).note == "dropped at the office"


def test_an_unknown_payment(payments: PaymentService) -> None:
    with pytest.raises(Conflict, match="no payment"):
        payments.get("M-2024-999999")


def test_payment_references_run_in_sequence(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    first = payments.receive("d-200", Money.parse("100.00"), at=SOLD_AT)
    second = payments.receive("d-200", Money.parse("100.00"), at=SOLD_AT)
    assert first.id != second.id
    assert len(payments.for_party("d-200")) == 2


def test_paying_a_seller_clears_the_payable(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    payments.pay_out("d-100", Money.parse("7965.00"), method=Method.CHEQUE)
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.SELLER_PAYABLE, "d-100") == Money.zero()
    assert ledger.balance(Account.CASH) == Money.parse("-7965.00")


def test_a_seller_cannot_be_overpaid(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    with pytest.raises(PolicyViolation) as caught:
        payments.pay_out("d-100", Money.parse("9000.00"))
    assert caught.value.rule == "payment.overpay"


def test_a_hold_stops_a_payout(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    with pytest.raises(PolicyViolation) as caught:
        payments.pay_out("d-100", Money.parse("100.00"), holds=["the title is expected"])
    assert caught.value.rule == "payment.held"
    assert "the title is expected" in str(caught.value)


def test_which_methods_clear_the_same_day() -> None:
    assert Method.WIRE.clears_immediately
    assert Method.CASH.clears_immediately
    assert Method.FLOOR_PLAN.clears_immediately
    assert not Method.CHEQUE.clears_immediately
    assert not Method.CARD.clears_immediately


def test_terms_are_counted_in_business_days(payments: PaymentService) -> None:
    assert BUYER_TERMS.unit.value == "business_days"
    due = payments.due_date(datetime(2024, 3, 12, 21, 0))
    assert due.date().isoformat() == "2024-03-14"


def test_a_holiday_weekend_does_not_make_a_buyer_late(
    payments: PaymentService,
) -> None:
    sold = datetime(2024, 11, 27, 16, 0)
    assert not payments.is_late(sold, datetime(2024, 11, 29, 12, 0))
    assert payments.is_late(sold, datetime(2024, 12, 5, 12, 0))


def test_a_payment_posts_one_balanced_entry(floor: Floor, payments: PaymentService) -> None:
    posted(floor)
    payments.receive("d-200", Money.parse("8545.00"))
    payments.pay_out("d-100", Money.parse("7965.00"))
    assert Ledger(floor.conn).is_balanced()
