from datetime import datetime, timedelta

import pytest

from autozest.arbitration.claims import Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import Conflict, PolicyViolation
from autozest.money import Money
from autozest.settlement.fees import Service
from autozest.settlement.ledger import Account, Ledger
from autozest.settlement.postings import PostingService
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def posting(floor: Floor) -> PostingService:
    return PostingService(floor.conn)


def sold(floor: Floor, price: str = "8200.00") -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if "if" in bidding.fall(run_id).status.value:
        bidding.accept(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    return run_id


def resolved(floor: Floor, run_id: int, resolution: Resolution, amount: str | None) -> str:
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    ResolutionService(floor.conn).resolve(
        claim.id, resolution, amount=Money.parse(amount) if amount else None, reason="agreed"
    )
    return claim.id


def test_the_figures_on_a_sold_unit(floor: Floor, posting: PostingService) -> None:
    figures = posting.figures(sold(floor))
    assert figures.hammer == Money.parse("8200.00")
    assert figures.buyer_fee == Money.parse("345.00")
    assert figures.seller_fee == Money.parse("235.00")
    assert figures.buyer_owes == Money.parse("8545.00")
    assert figures.seller_earns == Money.parse("7965.00")
    assert figures.auction_earns == Money.parse("580.00")


def test_nothing_to_settle_on_a_unit_that_did_not_sell(
    floor: Floor, posting: PostingService
) -> None:
    run_id = floor.catalogue()
    floor.sales.open_sale(floor.code)
    with pytest.raises(PolicyViolation) as caught:
        posting.figures(run_id)
    assert caught.value.rule == "settle.not_sold"


def test_posting_a_sale_lands_on_three_accounts(floor: Floor, posting: PostingService) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.parse("8545.00")
    assert ledger.balance(Account.SELLER_PAYABLE, "d-100") == Money.parse("7965.00")
    assert ledger.balance(Account.FEE_INCOME) == Money.parse("580.00")
    assert ledger.is_balanced()


def test_a_run_is_posted_once(floor: Floor, posting: PostingService) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    assert posting.is_posted(run_id)
    with pytest.raises(Conflict, match="already posted"):
        posting.post_sale(run_id)


def test_a_buyer_service_is_charged_to_the_buyer(floor: Floor, posting: PostingService) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    posting.post_service(run_id, Service.POST_SALE_INSPECTION)
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.parse("8685.00")
    assert ledger.balance(Account.SERVICE_INCOME) == Money.parse("140.00")


def test_a_seller_service_comes_out_of_the_proceeds(
    floor: Floor, posting: PostingService
) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    posting.post_service(run_id, Service.DETAIL)
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.SELLER_PAYABLE, "d-100") == Money.parse("7870.00")


def test_a_service_can_be_charged_at_a_different_price(
    floor: Floor, posting: PostingService
) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    posting.post_service(run_id, Service.TRANSPORT, amount=Money.parse("400.00"))
    assert Ledger(floor.conn).balance(Account.SERVICE_INCOME) == Money.parse("400.00")


def test_an_allowance_moves_money_without_moving_the_price(
    floor: Floor, posting: PostingService
) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    claim = resolved(floor, run_id, Resolution.REPAIR_ALLOWANCE, "600.00")
    posting.post_arbitration(claim)
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.parse("7945.00")
    assert ledger.balance(Account.SELLER_PAYABLE, "d-100") == Money.parse("7365.00")
    assert ledger.balance(Account.FEE_INCOME) == Money.parse("580.00")
    assert ledger.is_balanced()


def test_an_adjustment_reposts_the_whole_sale(floor: Floor, posting: PostingService) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    claim = resolved(floor, run_id, Resolution.PRICE_ADJUSTMENT, "600.00")
    posting.post_arbitration(claim)
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.parse("7945.00")
    assert ledger.is_balanced()


def test_an_adjustment_across_a_band_moves_the_fee_as_well(
    floor: Floor, posting: PostingService
) -> None:
    run_id = sold(floor, "10200.00")
    posting.post_sale(run_id)
    before = Ledger(floor.conn).balance(Account.FEE_INCOME)
    assert before == Money.parse("715.00")
    claim = resolved(floor, run_id, Resolution.PRICE_ADJUSTMENT, "600.00")
    posting.post_arbitration(claim)
    after = Ledger(floor.conn).balance(Account.FEE_INCOME)
    assert after == Money.parse("580.00")
    assert after < before


def test_an_adjustment_inside_a_band_leaves_the_fee_alone(
    floor: Floor, posting: PostingService
) -> None:
    run_id = sold(floor, "9500.00")
    posting.post_sale(run_id)
    claim = resolved(floor, run_id, Resolution.PRICE_ADJUSTMENT, "300.00")
    posting.post_arbitration(claim)
    assert Ledger(floor.conn).balance(Account.FEE_INCOME) == Money.parse("580.00")


def test_a_split_puts_the_auctions_share_on_its_own_account(
    floor: Floor, posting: PostingService
) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    claim = resolved(floor, run_id, Resolution.SPLIT, "600.00")
    posting.post_arbitration(claim)
    ledger = Ledger(floor.conn)
    assert ledger.is_balanced()
    assert ledger.balance(Account.BUYER_RECEIVABLE, "d-200") == Money.parse("7945.00")


def test_a_denial_posts_nothing(floor: Floor, posting: PostingService) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    claim = resolved(floor, run_id, Resolution.DENIED, None)
    with pytest.raises(PolicyViolation) as caught:
        posting.post_arbitration(claim)
    assert caught.value.rule == "settle.nothing_to_post"


def test_reposting_something_never_posted(floor: Floor, posting: PostingService) -> None:
    run_id = sold(floor)
    with pytest.raises(PolicyViolation) as caught:
        posting.repost_sale(run_id, "no reason")
    assert caught.value.rule == "settle.not_posted"


def test_a_reposted_sale_keeps_both_entries_on_the_record(
    floor: Floor, posting: PostingService
) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    posting.repost_sale(run_id, "correction")
    entries = Ledger(floor.conn).entries_for("run", str(run_id))
    assert len(entries) == 3
    assert any("reversal" in entry.memo for entry in entries)


def test_a_batch_posts_everything_in_one_go(floor: Floor, posting: PostingService) -> None:
    first = floor.catalogue(floor_price="3000.00")
    second = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    for run_id in (first, second):
        bidding.bid(run_id, "d-200", Money.parse("8200.00"))
        bidding.fall(run_id)
    entries = posting.post_sale_batch([first, second])
    assert len(entries) == 2
    assert Ledger(floor.conn).is_balanced()
    assert not floor.conn.in_transaction


def test_a_batch_skips_what_is_already_posted(floor: Floor, posting: PostingService) -> None:
    run_id = sold(floor)
    posting.post_sale(run_id)
    assert posting.post_sale_batch([run_id]) == []


def test_a_batch_that_fails_leaves_nothing(floor: Floor, posting: PostingService) -> None:
    first = floor.catalogue(floor_price="3000.00")
    second = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(first, "d-200", Money.parse("8200.00"))
    bidding.fall(first)
    with pytest.raises(PolicyViolation):
        posting.post_sale_batch([first, second])
    assert not posting.is_posted(first)
    assert not floor.conn.in_transaction
