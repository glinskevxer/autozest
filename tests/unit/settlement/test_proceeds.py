from datetime import datetime, timedelta

import pytest

from autozest.arbitration.desk import ArbitrationDesk
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import PolicyViolation
from autozest.money import Money
from autozest.settlement.ledger import Account, Ledger
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.proceeds import ProceedsService
from autozest.title.service import TitleService
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def proceeds(floor: Floor) -> ProceedsService:
    return ProceedsService(floor.conn)


def sold(floor: Floor, price: str = "8200.00") -> tuple[str, int]:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    PostingService(floor.conn).post_sale(run_id)
    return floor.sales.run(run_id).consignment_id, run_id


def clear_everything(floor: Floor, consignment_id: str) -> None:
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    titles.receive(consignment_id)


def test_an_unsold_unit_is_held_on_everything(floor: Floor, proceeds: ProceedsService) -> None:
    run_id = floor.catalogue()
    consignment_id = floor.sales.run(run_id).consignment_id
    position = proceeds.position(consignment_id)
    codes = [hold.code for hold in position.holds]
    assert "not_sold" in codes
    assert "no_run" in codes
    assert not position.is_clear


def test_a_sold_unit_is_held_until_the_buyer_pays(
    floor: Floor, proceeds: ProceedsService
) -> None:
    consignment_id, _ = sold(floor)
    position = proceeds.position(consignment_id)
    codes = [hold.code for hold in position.holds]
    assert "buyer_unpaid" in codes
    assert "no_title_record" in codes
    assert position.next_hold is not None


def test_paying_clears_one_hold_and_leaves_the_title(
    floor: Floor, proceeds: ProceedsService
) -> None:
    consignment_id, _ = sold(floor)
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    codes = [hold.code for hold in proceeds.position(consignment_id).holds]
    assert "buyer_unpaid" not in codes
    assert "no_title_record" in codes


def test_an_open_claim_holds_the_money(floor: Floor, proceeds: ProceedsService) -> None:
    consignment_id, run_id = sold(floor)
    clear_everything(floor, consignment_id)
    assert proceeds.position(consignment_id).is_clear
    ArbitrationDesk(floor.conn).open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    codes = [hold.code for hold in proceeds.position(consignment_id).holds]
    assert "arbitration_open" in codes


def test_a_clear_unit_releases_the_whole_balance(
    floor: Floor, proceeds: ProceedsService
) -> None:
    consignment_id, _ = sold(floor)
    clear_everything(floor, consignment_id)
    paid = proceeds.release(consignment_id)
    assert paid == Money.parse("7965.00")
    assert Ledger(floor.conn).balance(Account.SELLER_PAYABLE, "d-100") == Money.zero()


def test_a_held_unit_refuses_and_lists_the_reasons(
    floor: Floor, proceeds: ProceedsService
) -> None:
    consignment_id, _ = sold(floor)
    with pytest.raises(PolicyViolation) as caught:
        proceeds.release(consignment_id)
    assert caught.value.rule == "proceeds.held"
    assert caught.value.context["holds"] == 2


def test_a_lien_comes_off_the_top(floor: Floor, proceeds: ProceedsService) -> None:
    consignment_id, _ = sold(floor)
    clear_everything(floor, consignment_id)
    paid = proceeds.release(consignment_id, lien=Money.parse("6000.00"), lienholder="f-1")
    assert paid == Money.parse("1965.00")
    ledger = Ledger(floor.conn)
    assert ledger.balance(Account.LIEN_CLEARING, "f-1") == Money.parse("6000.00")
    assert ledger.is_balanced()


def test_a_lien_with_nobody_to_pay_it_to(floor: Floor, proceeds: ProceedsService) -> None:
    consignment_id, _ = sold(floor)
    clear_everything(floor, consignment_id)
    with pytest.raises(PolicyViolation) as caught:
        proceeds.release(consignment_id, lien=Money.parse("6000.00"))
    assert caught.value.rule == "proceeds.no_lienholder"


def test_a_lien_bigger_than_the_proceeds_pays_the_seller_nothing(
    floor: Floor, proceeds: ProceedsService
) -> None:
    consignment_id, _ = sold(floor)
    clear_everything(floor, consignment_id)
    assert (
        proceeds.release(consignment_id, lien=Money.parse("9000.00"), lienholder="f-1")
        == Money.zero()
    )


def test_the_position_shows_gross_and_net(floor: Floor, proceeds: ProceedsService) -> None:
    consignment_id, _ = sold(floor)
    position = proceeds.position(consignment_id, Money.parse("1000.00"))
    assert position.gross == Money.parse("7965.00")
    assert position.net == Money.parse("6965.00")
    assert position.seller_id == "d-100"


def test_the_ready_and_held_lists(floor: Floor, proceeds: ProceedsService) -> None:
    consignment_id, _ = sold(floor)
    assert proceeds.ready_to_release() == []
    assert consignment_id in proceeds.held()
    clear_everything(floor, consignment_id)
    assert proceeds.ready_to_release() == [consignment_id]
    assert proceeds.held() == {}
