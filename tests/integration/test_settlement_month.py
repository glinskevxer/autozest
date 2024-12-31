"""A month of settlement, from the block to a closed period.

The sale night test in test_sale_night walks one unit. This walks the money:
several units, several buyers, a bank file, an arbitration that moves a fee
band, a write off, and a period that will not close until all of it is
straight. Every number here was worked out by hand before it was asserted.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from autozest.arbitration.claims import Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.block.catalogue import CatalogueService
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import ConsignmentStatus
from autozest.defects import DefectClass
from autozest.errors import PolicyViolation
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.settlement.adjustments import AdjustmentService
from autozest.settlement.floorplan import FloorplanService
from autozest.settlement.ledger import Account as LedgerAccount
from autozest.settlement.ledger import Ledger
from autozest.settlement.payments import PaymentService
from autozest.settlement.periods import PeriodService, PeriodStatus
from autozest.settlement.postings import PostingService
from autozest.settlement.proceeds import ProceedsService
from autozest.settlement.reconcile import Reconciler, parse
from autozest.settlement.statements import StatementService
from autozest.title.service import TitleService
from autozest.vin import Vin

SALE_DAY = date(2024, 3, 12)
SOLD_AT = datetime(2024, 3, 12, 14, 30)

VINS = ["1HGCM82633A004352", "5YJ3E1EA0KF000316", "JTDKN3DU8A0000001"]


@pytest.fixture()
def month(db: sqlite3.Connection) -> sqlite3.Connection:
    accounts = AccountRepository(db)
    for account_id, name in (
        ("d-100", "Ridgeline Motors"),
        ("d-200", "Kestrel Auto"),
        ("d-300", "Cotton Lane Motors"),
    ):
        accounts.add(
            Account(
                id=account_id,
                name=name,
                account_class=AccountClass.INDEPENDENT_DEALER,
                dealer_licence=f"TX-{account_id}",
                state="TX",
            )
        )
    vehicles = VehicleRepository(db)
    for index, vin in enumerate(VINS):
        vehicles.add(Vehicle(vin=Vin.parse(vin), make="Make", model=f"Model{index}"))
    SaleRepository(db).create(SALE_DAY, "Tuesday dealer sale", {"A": 100})
    return db


def catalogued(conn: sqlite3.Connection, vin: str, floor: str) -> int:
    intake = IntakeService(conn)
    inspection = InspectionService(conn)
    unit = intake.register(vin, "d-100", floor=Money.parse(floor))
    intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
    report = inspection.open_report(unit.id, "j.hale")
    inspection.finalise(report.id, reference_value=Money.parse("30000.00"))
    placement = CatalogueService(conn).place("S-20240312", unit.id, "A")
    run = SaleRepository(conn).run_at("S-20240312", placement.lane, placement.position)
    assert run.id is not None
    return run.id


def sell(conn: sqlite3.Connection, run_id: int, buyer: str, price: str) -> None:
    bidding = BiddingService(conn)
    bidding.bid(run_id, buyer, Money.parse(price))
    if bidding.fall(run_id).status.value == "if_bid":
        bidding.accept(run_id)
    conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))


def test_three_units_two_buyers_and_a_period_that_will_not_close(
    month: sqlite3.Connection,
) -> None:
    first = catalogued(month, VINS[0], "5000.00")
    second = catalogued(month, VINS[1], "5000.00")
    third = catalogued(month, VINS[2], "5000.00")
    SaleRepository(month).open_sale("S-20240312")

    sell(month, first, "d-200", "10200.00")
    sell(month, second, "d-200", "6400.00")
    sell(month, third, "d-300", "8200.00")

    posting = PostingService(month)
    for run_id in (first, second, third):
        posting.post_sale(run_id)

    statements = StatementService(month)
    # 10200 + 425, 6400 + 285, so d-200 owes 17,310.
    assert statements.invoice("d-200").balance == Money.parse("17310.00")
    assert statements.invoice("d-300").balance == Money.parse("8545.00")
    # Sellers are all d-100: 9910 + 6205 + 7965.
    assert statements.seller_statement("d-100").balance == Money.parse("24080.00")

    periods = PeriodService(month)
    periods.open_period(date(2024, 3, 1), date(2024, 3, 31))
    periods.begin_close("P-202403")
    assert all(check.passed for check in periods.checks("P-202403"))
    periods.reopen("P-202403")

    desk = ArbitrationDesk(month)
    claim = desk.open_claim(
        first, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    periods.begin_close("P-202403")
    with pytest.raises(PolicyViolation) as caught:
        periods.close("P-202403", closed_by="m.byrne")
    assert caught.value.rule == "period.checks_failed"
    periods.reopen("P-202403")

    desk.take_up(claim.id, "desk")
    ResolutionService(month).resolve(
        claim.id,
        Resolution.PRICE_ADJUSTMENT,
        amount=Money.parse("600.00"),
        reason="agreed with both sides",
    )
    posting.post_arbitration(claim.id)

    # 10200 drops to 9600, which crosses a band: the fee falls 425 to 345.
    assert statements.invoice("d-200").balance == Money.parse("16630.00")

    bank = parse(
        "date,amount,reference\n2024-03-14,16630.00,WIRE-1\n2024-03-14,8545.00,WIRE-2\n"
    )
    reconciler = Reconciler(month)
    result = reconciler.match(bank)
    assert len(result.matched) == 2
    assert reconciler.apply(result) == 2
    assert statements.invoice("d-200").balance == Money.zero()

    titles = TitleService(month)
    proceeds = ProceedsService(month)
    for run_id in (first, second, third):
        consignment_id = SaleRepository(month).run(run_id).consignment_id
        titles.expect(consignment_id)
        titles.receive(consignment_id)
        assert proceeds.position(consignment_id).is_clear
        proceeds.release(consignment_id)

    ledger = Ledger(month)
    assert ledger.is_balanced()
    assert ledger.balance(LedgerAccount.SELLER_PAYABLE, "d-100") == Money.zero()

    periods.begin_close("P-202403")
    closed = periods.close("P-202403", closed_by="m.byrne")
    assert closed.status is PeriodStatus.CLOSED
    with pytest.raises(PolicyViolation):
        periods.require_open(date(2024, 3, 20))


def test_a_write_off_and_a_credit_line_narrow_together(
    month: sqlite3.Connection,
) -> None:
    run_id = catalogued(month, VINS[0], "5000.00")
    SaleRepository(month).open_sale("S-20240312")
    sell(month, run_id, "d-200", "8200.00")
    PostingService(month).post_sale(run_id)

    credit = FloorplanService(month)
    credit.open_line("L-1", "d-200", "Ninth Street", Money.parse("20000.00"))
    assert credit.capacity("d-200").available == Money.parse("11455.00")

    adjustments = AdjustmentService(month)
    adjustments.write_off(
        "d-200",
        Money.parse("545.00"),
        reason="fee dispute settled",
        raised_by="p.dunne",
        approved_by="m.byrne",
    )
    assert credit.capacity("d-200").available == Money.parse("12000.00")

    PaymentService(month).receive("d-200", Money.parse("8000.00"), reference="WIRE-3")
    assert StatementService(month).invoice("d-200").balance == Money.zero()
    assert credit.capacity("d-200").available == Money.parse("20000.00")
    assert Ledger(month).is_balanced()


def test_a_unit_that_did_not_sell_costs_the_seller_and_goes_home(
    month: sqlite3.Connection,
) -> None:
    run_id = catalogued(month, VINS[0], "30000.00")
    SaleRepository(month).open_sale("S-20240312")
    BiddingService(month).fall(run_id)
    consignment_id = SaleRepository(month).run(run_id).consignment_id
    intake = IntakeService(month)
    assert intake.consignments.get(consignment_id).status is ConsignmentStatus.NO_SALE

    titles = TitleService(month)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    titles.release_to_seller(consignment_id)
    assert intake.release(consignment_id).status is ConsignmentStatus.RELEASED
    assert Ledger(month).is_balanced()
