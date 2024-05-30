import pytest

from autozest.block.bidding import BiddingService
from autozest.money import Money
from autozest.settlement.fees import Service
from autozest.settlement.postings import PostingService
from autozest.settlement.statements import Side, StatementService
from tests.conftest import Floor


@pytest.fixture()
def statements(floor: Floor) -> StatementService:
    return StatementService(floor.conn)


def sold(floor: Floor, price: str = "8200.00") -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    return run_id


def test_a_party_with_nothing_posted_has_an_empty_statement(
    statements: StatementService,
) -> None:
    statement = statements.invoice("d-200")
    assert statement.lines == ()
    assert statement.balance == Money.zero()
    assert statement.is_settled
    assert statement.built_at is not None


def test_a_buyer_invoice_shows_what_they_owe(
    floor: Floor, statements: StatementService
) -> None:
    posting = PostingService(floor.conn)
    posting.post_sale(sold(floor))
    invoice = statements.invoice("d-200")
    assert invoice.balance == Money.parse("8545.00")
    assert invoice.owes_us
    assert len(invoice.lines) == 1
    assert "sale of" in invoice.lines[0].memo


def test_a_seller_statement_shows_what_they_are_owed(
    floor: Floor, statements: StatementService
) -> None:
    posting = PostingService(floor.conn)
    posting.post_sale(sold(floor))
    statement = statements.seller_statement("d-100")
    assert statement.balance == Money.parse("7965.00")
    assert not statement.owes_us
    assert statement.side is Side.SELLER


def test_both_sides_read_the_same_way_round(floor: Floor, statements: StatementService) -> None:
    posting = PostingService(floor.conn)
    run_id = sold(floor)
    posting.post_sale(run_id)
    assert statements.invoice("d-200").balance.cents > 0
    assert statements.seller_statement("d-100").balance.cents > 0


def test_services_appear_as_further_lines(floor: Floor, statements: StatementService) -> None:
    posting = PostingService(floor.conn)
    run_id = sold(floor)
    posting.post_sale(run_id)
    posting.post_service(run_id, Service.POST_SALE_INSPECTION)
    posting.post_service(run_id, Service.DETAIL)
    invoice = statements.invoice("d-200")
    statement = statements.seller_statement("d-100")
    assert len(invoice.lines) == 2
    assert invoice.balance == Money.parse("8685.00")
    assert statement.balance == Money.parse("7870.00")


def test_a_statement_narrows_to_one_sale(floor: Floor, statements: StatementService) -> None:
    posting = PostingService(floor.conn)
    posting.post_sale(sold(floor))
    assert statements.invoice("d-200", floor.code).balance == Money.parse("8545.00")
    assert statements.invoice("d-200", "S-19990101").balance == Money.zero()


def test_charges_and_credits_are_separated(floor: Floor, statements: StatementService) -> None:
    posting = PostingService(floor.conn)
    run_id = sold(floor)
    posting.post_sale(run_id)
    posting.repost_sale(run_id, "correction")
    invoice = statements.invoice("d-200")
    assert invoice.charges == Money.parse("17090.00")
    assert invoice.credits == Money.parse("-8545.00")
    assert invoice.balance == Money.parse("8545.00")
    assert any(line.is_credit for line in invoice.lines)


def test_the_outstanding_lists(floor: Floor, statements: StatementService) -> None:
    posting = PostingService(floor.conn)
    posting.post_sale(sold(floor))
    assert statements.outstanding_buyers() == {"d-200": Money.parse("8545.00")}
    assert statements.unpaid_sellers() == {"d-100": Money.parse("7965.00")}


def test_a_settled_party_drops_off_the_list(floor: Floor, statements: StatementService) -> None:
    posting = PostingService(floor.conn)
    run_id = sold(floor)
    posting.post_sale(run_id)
    posting.repost_sale(run_id, "cancelled")
    entries = posting.ledger.entries_for("run", str(run_id))
    assert entries[-1].id is not None
    posting.ledger.reverse(entries[-1].id, "cancelled for good")
    assert statements.outstanding_buyers() == {}


def test_the_sale_totals_for_the_office_board(
    floor: Floor, statements: StatementService
) -> None:
    posting = PostingService(floor.conn)
    posting.post_sale(sold(floor))
    totals = statements.sale_totals(floor.code)
    assert totals["buyer_receivable"] == Money.parse("8545.00")
    assert totals["fee_income"] == Money.parse("-580.00")
