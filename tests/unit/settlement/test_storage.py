from datetime import timedelta

import pytest

from autozest.block.bidding import BiddingService
from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.consignment.models import ConsignmentStatus
from autozest.errors import ValidationError
from autozest.money import Money
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.storage import (
    FREE_DAYS_CONTRACT,
    FREE_DAYS_NO_SALE,
    FREE_DAYS_SOLD,
    StorageService,
)
from autozest.title.service import TitleService
from tests.conftest import SALE_DAY, Floor

LATER = SALE_DAY + timedelta(days=20)


@pytest.fixture()
def storage(floor: Floor) -> StorageService:
    return StorageService(floor.conn)


def sold(floor: Floor, price: str = "8200.00") -> str:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    return floor.sales.run(run_id).consignment_id


def no_sale(floor: Floor) -> str:
    run_id = floor.catalogue(floor_price="30000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    BiddingService(floor.conn).fall(run_id)
    return floor.sales.run(run_id).consignment_id


def clear_holds(floor: Floor, consignment_id: str) -> None:
    run = floor.sales.run_for_consignment(consignment_id)
    assert run is not None and run.id is not None
    PostingService(floor.conn).post_sale(run.id)
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    titles.receive(consignment_id)


def test_a_sold_unit_gets_a_short_window(floor: Floor, storage: StorageService) -> None:
    consignment_id = sold(floor)
    assert storage.free_days_for(consignment_id) == FREE_DAYS_SOLD


def test_a_no_sale_gets_longer(floor: Floor, storage: StorageService) -> None:
    consignment_id = no_sale(floor)
    assert storage.free_days_for(consignment_id) == FREE_DAYS_NO_SALE


def test_a_fleet_contract_gets_longer_still(floor: Floor, storage: StorageService) -> None:
    AccountRepository(floor.conn).add(
        Account(
            id="fl-1",
            name="Halloway Fleet",
            account_class=AccountClass.FLEET_LEASE,
            dealer_licence="TX-9",
            state="TX",
        )
    )
    run_id = floor.catalogue(floor_price="3000.00", seller="fl-1")
    consignment_id = floor.sales.run(run_id).consignment_id
    assert storage.free_days_for(consignment_id) == FREE_DAYS_CONTRACT


def test_nothing_is_charged_inside_the_free_days(floor: Floor, storage: StorageService) -> None:
    consignment_id = sold(floor)
    clear_holds(floor, consignment_id)
    charge = storage.charge_for(consignment_id, SALE_DAY + timedelta(days=3))
    assert charge.is_free
    assert charge.amount == Money.zero()
    assert "inside the 5 free days" in charge.explain()


def test_days_past_the_free_period_are_charged(floor: Floor, storage: StorageService) -> None:
    consignment_id = sold(floor)
    clear_holds(floor, consignment_id)
    charge = storage.charge_for(consignment_id, LATER)
    assert charge.charged_days == 15
    assert charge.amount == Money.parse("270.00")
    assert "15 days at 18.00" in charge.explain()


def test_a_sold_unit_is_charged_to_the_buyer(floor: Floor, storage: StorageService) -> None:
    consignment_id = sold(floor)
    clear_holds(floor, consignment_id)
    assert storage.charge_for(consignment_id, LATER).payer_id == "d-200"


def test_a_no_sale_is_charged_to_the_seller(floor: Floor, storage: StorageService) -> None:
    consignment_id = no_sale(floor)
    assert storage.charge_for(consignment_id, LATER).payer_id == "d-100"


def test_a_unit_we_are_holding_is_not_charged(floor: Floor, storage: StorageService) -> None:
    consignment_id = sold(floor)
    charge = storage.charge_for(consignment_id, LATER)
    assert charge.held
    assert charge.amount == Money.zero()
    assert "held behind something we own" in charge.explain()


def test_the_charge_starts_once_the_hold_clears(floor: Floor, storage: StorageService) -> None:
    consignment_id = sold(floor)
    assert storage.charge_for(consignment_id, LATER).held
    clear_holds(floor, consignment_id)
    assert not storage.charge_for(consignment_id, LATER).held


def test_a_unit_that_never_arrived_has_nothing_to_charge(
    floor: Floor, storage: StorageService
) -> None:
    unit = floor.intake.register("KMHD35LEXEU000001", "d-100")
    with pytest.raises(ValidationError, match="never arrived"):
        storage.charge_for(unit.id, LATER)


def test_the_outstanding_list_leaves_out_the_free_ones(
    floor: Floor, storage: StorageService
) -> None:
    sold_run = floor.catalogue(floor_price="3000.00")
    unsold_run = floor.catalogue(floor_price="30000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(sold_run, "d-200", Money.parse("8200.00"))
    bidding.fall(sold_run)
    bidding.fall(unsold_run)
    first = floor.sales.run(sold_run).consignment_id
    clear_holds(floor, first)
    assert [c.consignment_id for c in storage.outstanding(SALE_DAY + timedelta(days=8))] == [
        first
    ]
    assert len(storage.outstanding(LATER)) == 2


def test_a_no_sale_that_is_still_here_is_charged(floor: Floor, storage: StorageService) -> None:
    consignment_id = no_sale(floor)
    assert floor.intake.consignments.get(consignment_id).status is ConsignmentStatus.NO_SALE
    charge = storage.charge_for(consignment_id, LATER)
    assert charge.charged_days == 10
    assert charge.amount == Money.parse("180.00")
