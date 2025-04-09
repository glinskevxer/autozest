from datetime import date

from autozest.block.bidding import BiddingService
from autozest.money import Money
from autozest.reporting import documents
from autozest.reporting.documents import payment_reference
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.statements import Side
from tests.conftest import Floor

TODAY = date(2024, 3, 14)


def owing(floor: Floor) -> None:
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    PostingService(floor.conn).post_sale(run_id)


def test_a_reference_is_short_and_traceable() -> None:
    assert payment_reference(Side.BUYER, "d-200", TODAY) == "BD200-240314"
    assert payment_reference(Side.SELLER, "d-100", TODAY) == "SD100-240314"


def test_an_invoice_reads_as_a_document(floor: Floor) -> None:
    owing(floor)
    built = documents.build(floor.conn, Side.BUYER, "d-200", on=TODAY)
    text = built.text()
    assert "INVOICE" in text
    assert "Kestrel Auto (d-200)" in text
    assert "8,545.00" in text
    assert built.reference in text
    assert built.balance == Money.parse("8545.00")


def test_an_invoice_says_what_to_send_and_when(floor: Floor) -> None:
    owing(floor)
    built = documents.build(floor.conn, Side.BUYER, "d-200", on=TODAY)
    text = " ".join(built.text().split())
    assert "Please send 8,545.00" in text
    assert built.reference in text
    assert "2 business days" in text
    assert built.due_on is not None


def test_a_seller_statement_says_what_holds_it(floor: Floor) -> None:
    owing(floor)
    built = documents.build(floor.conn, Side.SELLER, "d-100", on=TODAY)
    text = " ".join(built.text().split())
    assert "SELLER STATEMENT" in built.text()
    assert "7,965.00 is due to you" in text
    assert "no arbitration is open" in text
    assert built.due_on is None


def test_a_settled_account_says_so(floor: Floor) -> None:
    owing(floor)
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    built = documents.build(floor.conn, Side.BUYER, "d-200", on=TODAY)
    assert "Nothing is outstanding" in built.text()


def test_a_document_narrows_to_one_sale(floor: Floor) -> None:
    owing(floor)
    built = documents.build(floor.conn, Side.BUYER, "d-200", sale_code=floor.code, on=TODAY)
    assert floor.code in built.text()
    assert built.balance == Money.parse("8545.00")


def test_nothing_runs_off_the_page(floor: Floor) -> None:
    owing(floor)
    for side, party in ((Side.BUYER, "d-200"), (Side.SELLER, "d-100")):
        built = documents.build(floor.conn, side, party, on=TODAY)
        assert all(len(line) <= 92 for line in built.lines)


def test_the_auction_name_comes_off_the_settings(floor: Floor) -> None:
    from autozest.config import from_mapping

    owing(floor)
    settings = from_mapping({"auction_name": "Cedar Ridge Auto Auction"})
    built = documents.build(floor.conn, Side.BUYER, "d-200", settings=settings, on=TODAY)
    assert "Cedar Ridge Auto Auction" in built.text()
