"""Three sales, a rerun, a buy back and two closed months.

The other two integration tests each walk one thing all the way through. This
one walks time: a unit that misses and comes back, a title that arrives branded
weeks after the sale, and periods closing behind it all.

It is the test that has caught the things that only go wrong when something
happens after something else.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from autozest.arbitration.claims import ClaimStatus, Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.block.catalogue import CatalogueService
from autozest.block.models import RunStatus
from autozest.block.rerun import RerunService
from autozest.block.sales import SaleRepository
from autozest.block.templates import TemplateService
from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import ConsignmentStatus
from autozest.defects import DefectClass
from autozest.errors import PolicyViolation
from autozest.inspection.announcements import Light
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.reporting import concentration, disclosure
from autozest.settlement.ledger import Ledger
from autozest.settlement.payments import PaymentService
from autozest.settlement.periods import PeriodService
from autozest.settlement.postings import PostingService
from autozest.title.branding import TitleBrand
from autozest.title.service import TitleService
from autozest.vin import Vin

MARCH = date(2024, 3, 12)
APRIL = date(2024, 4, 9)
MAY = date(2024, 5, 14)

VINS = [
    "1HGCM82633A004352",
    "5YJ3E1EA0KF000316",
    "JTDKN3DU8A0000001",
    "WBA3A5C52DF000001",
]


@pytest.fixture()
def quarter(db: sqlite3.Connection) -> sqlite3.Connection:
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
    TemplateService(db).create("t-1", "Tuesday dealer", 1, "Tuesday dealer sale", {"A": 100})
    return db


def sale_on(conn: sqlite3.Connection, day: date) -> str:
    return TemplateService(conn).build("t-1", day).code


def catalogued(conn: sqlite3.Connection, vin: str, sale_code: str, floor: str) -> int:
    intake = IntakeService(conn)
    inspection = InspectionService(conn)
    unit = intake.register(vin, "d-100", floor=Money.parse(floor))
    intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
    report = inspection.open_report(unit.id, "j.hale")
    inspection.finalise(report.id, reference_value=Money.parse("30000.00"))
    placement = CatalogueService(conn).place(sale_code, unit.id, "A")
    run = SaleRepository(conn).run_at(sale_code, placement.lane, placement.position)
    assert run.id is not None
    return run.id


def run_it(
    conn: sqlite3.Connection, run_id: int, buyer: str | None, price: str | None, day: date
) -> None:
    bidding = BiddingService(conn)
    if buyer and price:
        bidding.bid(run_id, buyer, Money.parse(price))
    if bidding.fall(run_id).status is RunStatus.IF_BID:
        bidding.accept(run_id)
    conn.execute(
        "UPDATE run SET ran_at = ? WHERE id = ?",
        (datetime.combine(day, datetime.min.time()).isoformat(), run_id),
    )


def test_a_unit_misses_in_march_and_sells_in_april(quarter: sqlite3.Connection) -> None:
    march = sale_on(quarter, MARCH)
    run_id = catalogued(quarter, VINS[0], march, "30000.00")
    SaleRepository(quarter).open_sale(march)
    run_it(quarter, run_id, None, None, MARCH)
    SaleRepository(quarter).close_sale(march)

    intake = IntakeService(quarter)
    consignment_id = SaleRepository(quarter).run(run_id).consignment_id
    assert intake.consignments.get(consignment_id).status is ConsignmentStatus.NO_SALE

    reruns = RerunService(quarter)
    planned = reruns.plan(consignment_id)
    assert planned.previous_runs == 1
    assert planned.total_fees == Money.parse("165.00")

    april = sale_on(quarter, APRIL)
    intake.consignments.set_floor(consignment_id, Money.parse("5000.00"))
    reruns.rerun(consignment_id, april, "A")
    SaleRepository(quarter).open_sale(april)
    second = SaleRepository(quarter).runs(april)[0]
    assert second.id is not None
    run_it(quarter, second.id, "d-200", "8200.00", APRIL)

    assert intake.consignments.get(consignment_id).status is ConsignmentStatus.SOLD
    assert reruns.sold_after_rerun() == [consignment_id]

    ledger = Ledger(quarter)
    PostingService(quarter).post_sale(second.id)
    assert ledger.is_balanced()


def test_a_branded_title_lands_after_the_month_it_sold_in(
    quarter: sqlite3.Connection,
) -> None:
    march = sale_on(quarter, MARCH)
    run_id = catalogued(quarter, VINS[0], march, "3000.00")
    SaleRepository(quarter).open_sale(march)
    run_it(quarter, run_id, "d-200", "8200.00", MARCH)
    SaleRepository(quarter).close_sale(march)

    posting = PostingService(quarter)
    posting.post_sale(run_id)
    PaymentService(quarter).receive("d-200", Money.parse("8545.00"), reference="WIRE-1")

    periods = PeriodService(quarter)
    periods.open_period(date(2024, 3, 1), date(2024, 3, 31))
    periods.begin_close("P-202403")
    periods.close("P-202403", closed_by="m.byrne")

    consignment_id = SaleRepository(quarter).run(run_id).consignment_id
    titles = TitleService(quarter)
    titles.expect(consignment_id)
    _, surprise = titles.receive(consignment_id, brand=TitleBrand.SALVAGE)
    assert surprise is TitleBrand.SALVAGE

    audit = disclosure.audit(quarter, march)
    assert len(audit.misses) == 1
    assert not audit.is_clean

    desk = ArbitrationDesk(quarter)
    claim = desk.open_claim(
        run_id,
        "d-200",
        DefectClass.BRANDING,
        "salvage brand nobody called",
        at=datetime.combine(APRIL, datetime.min.time()),
    )
    desk.take_up(claim.id, "desk")
    outcome = ResolutionService(quarter).resolve(
        claim.id, Resolution.BUY_BACK, reason="undisclosed salvage brand"
    )
    assert outcome.unwinds
    assert outcome.buyer_receives == Money.parse("8200.00")
    assert desk.claims.get(claim.id).status is ClaimStatus.RESOLVED

    # March is shut. The correction belongs in the month that is open.
    periods.open_period(date(2024, 4, 1), date(2024, 4, 30))
    periods.require_open(APRIL)
    with pytest.raises(PolicyViolation) as shut:
        periods.require_open(date(2024, 3, 20))
    assert shut.value.rule == "period.closed"


def test_a_quarter_of_sales_and_who_they_rested_on(
    quarter: sqlite3.Connection,
) -> None:
    for day, vins, buyers in (
        (MARCH, VINS[:2], ["d-200", "d-200"]),
        (APRIL, VINS[2:3], ["d-300"]),
        (MAY, VINS[3:4], ["d-200"]),
    ):
        code = sale_on(quarter, day)
        run_ids = [catalogued(quarter, vin, code, "3000.00") for vin in vins]
        SaleRepository(quarter).open_sale(code)
        for run_id, buyer in zip(run_ids, buyers, strict=True):
            run_it(quarter, run_id, buyer, "6000.00", day)
        SaleRepository(quarter).close_sale(code)

    march_report = concentration.build(quarter, "S-20240312")
    assert march_report.gross == Money.parse("12000.00")
    assert march_report.heaviest_buyer is not None
    assert march_report.heaviest_buyer.party_id == "d-200"
    assert march_report.is_concentrated

    ledger = Ledger(quarter)
    posting = PostingService(quarter)
    for code in ("S-20240312", "S-20240409", "S-20240514"):
        posting.post_sale_batch(
            [run.id for run in SaleRepository(quarter).runs(code) if run.id]
        )
    assert ledger.is_balanced()


def test_the_light_is_recomputed_when_a_unit_comes_back(
    quarter: sqlite3.Connection,
) -> None:
    march = sale_on(quarter, MARCH)
    run_id = catalogued(quarter, VINS[0], march, "30000.00")
    SaleRepository(quarter).open_sale(march)
    run_it(quarter, run_id, None, None, MARCH)
    consignment_id = SaleRepository(quarter).run(run_id).consignment_id

    intake = IntakeService(quarter)
    intake.consignments.set_floor(consignment_id, Money.parse("900.00"))

    reruns = RerunService(quarter)
    planned = reruns.plan(consignment_id)
    assert planned.previous_light is Light.GREEN
    assert planned.new_light is Light.RED
    assert planned.light_changed

    april = sale_on(quarter, APRIL)
    reruns.rerun(consignment_id, april, "A", charge=False)
    assert SaleRepository(quarter).runs(april)[0].light is Light.RED
