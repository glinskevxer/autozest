"""One unit, all the way through, with nothing mocked.

These walk the same path the building walks on a Tuesday: a unit arrives, is
inspected, runs, sells, the buyer pays, the title turns up, the seller is paid.
The unit tests each check one rule. This checks that the rules add up, which is
a different question and the one that has actually broken twice.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from autozest.arbitration.claims import ClaimStatus, Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.block.catalogue import CatalogueService
from autozest.block.models import RunStatus, SaleStatus
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import ConsignmentStatus
from autozest.defects import DefectClass
from autozest.errors import PolicyViolation
from autozest.inspection.announcements import AnnouncementCode, Light
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.reporting import recap as recap_report
from autozest.settlement.ledger import Account as LedgerAccount
from autozest.settlement.ledger import Ledger
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.proceeds import ProceedsService
from autozest.title.branding import TitleBrand
from autozest.title.service import TitleService
from autozest.vin import Vin

SALE_DAY = date(2024, 3, 12)
SOLD_AT = datetime(2024, 3, 12, 14, 30)
ACCORD = "1HGCM82633A004352"


@pytest.fixture()
def house(db: sqlite3.Connection) -> sqlite3.Connection:
    accounts = AccountRepository(db)
    accounts.add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-1",
            state="TX",
        )
    )
    accounts.add(
        Account(
            id="d-200",
            name="Kestrel Auto",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-2",
            state="TX",
        )
    )
    VehicleRepository(db).add(
        Vehicle(vin=Vin.parse(ACCORD), make="Honda", model="Accord", model_year=2019)
    )
    SaleRepository(db).create(SALE_DAY, "Tuesday dealer sale", {"A": 100})
    return db


def run_to_the_block(
    conn: sqlite3.Connection,
    *,
    damage: DamageLine | None = None,
    floor: str = "5000.00",
    declare: frozenset[AnnouncementCode] = frozenset(),
) -> int:
    intake = IntakeService(conn)
    inspection = InspectionService(conn)
    unit = intake.register(ACCORD, "d-100", floor=Money.parse(floor))
    intake.check_in(unit.id, Arrival(keys_count=2, miles=41_930))
    report = inspection.open_report(unit.id, "j.hale")
    if damage is not None:
        inspection.add_damage(report.id, damage)
    inspection.finalise(report.id, reference_value=Money.parse("12000.00"), declared=declare)
    placement = CatalogueService(conn).place("S-20240312", unit.id, "A")
    run = SaleRepository(conn).run_at("S-20240312", placement.lane, placement.position)
    assert run.id is not None
    return run.id


def sell(conn: sqlite3.Connection, run_id: int, price: str = "8200.00") -> None:
    sales = SaleRepository(conn)
    if sales.get("S-20240312").status.accepts_catalogue_changes:
        sales.open_sale("S-20240312")
    bidding = BiddingService(conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    if bidding.fall(run_id).status is RunStatus.IF_BID:
        bidding.accept(run_id)
    conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))


def test_a_clean_unit_from_the_gate_to_the_sellers_cheque(
    house: sqlite3.Connection,
) -> None:
    run_id = run_to_the_block(house)
    run = SaleRepository(house).run(run_id)
    assert run.light is Light.GREEN

    sell(house, run_id)
    assert SaleRepository(house).run(run_id).status is RunStatus.SOLD

    posting = PostingService(house)
    posting.post_sale(run_id)
    proceeds = ProceedsService(house)
    consignment_id = run.consignment_id

    assert [hold.code for hold in proceeds.position(consignment_id).holds] == [
        "buyer_unpaid",
        "no_title_record",
    ]

    PaymentService(house).receive("d-200", Money.parse("8545.00"), reference="wire 1")
    titles = TitleService(house)
    titles.expect(consignment_id)
    _, surprise = titles.receive(consignment_id, issuing_state="TX")
    assert surprise is None

    assert proceeds.position(consignment_id).is_clear
    assert proceeds.release(consignment_id) == Money.parse("7965.00")
    assert titles.send(consignment_id).status.has_left

    ledger = Ledger(house)
    assert ledger.is_balanced()
    assert ledger.balance(LedgerAccount.CASH) == Money.parse("580.00")
    assert ledger.balance(LedgerAccount.FEE_INCOME) == Money.parse("580.00")


def test_a_bent_rail_narrows_everything_downstream(house: sqlite3.Connection) -> None:
    run_id = run_to_the_block(
        house,
        damage=DamageLine(
            panel=Panel.RAIL,
            kind=DamageKind.BENT,
            severity=2,
            estimate=Money.parse("100.00"),
        ),
    )
    run = SaleRepository(house).run(run_id)
    assert run.light is Light.YELLOW
    assert AnnouncementCode.UNIBODY_DAMAGE in InspectionService(house).announcements(
        run.consignment_id
    )

    sell(house, run_id)
    desk = ArbitrationDesk(house)

    with pytest.raises(PolicyViolation) as announced:
        desk.open_claim(
            run_id,
            "d-200",
            DefectClass.STRUCTURAL,
            "bent rail",
            at=SOLD_AT + timedelta(hours=1),
        )
    assert announced.value.rule == "arb.announced"

    with pytest.raises(PolicyViolation) as gated:
        desk.open_claim(
            run_id,
            "d-200",
            DefectClass.ENGINE,
            "knocking",
            at=SOLD_AT + timedelta(hours=1),
        )
    assert gated.value.rule == "arb.light"

    claim = desk.open_claim(
        run_id,
        "d-200",
        DefectClass.ODOMETER,
        "history shows a rollback",
        at=SOLD_AT + timedelta(days=20),
    )
    assert claim.status is ClaimStatus.OPEN


def test_an_adjustment_moves_the_fee_and_everything_downstream(
    house: sqlite3.Connection,
) -> None:
    run_id = run_to_the_block(house)
    sell(house, run_id, "10200.00")
    posting = PostingService(house)
    posting.post_sale(run_id)
    ledger = Ledger(house)
    assert ledger.balance(LedgerAccount.FEE_INCOME) == Money.parse("715.00")

    desk = ArbitrationDesk(house)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    ResolutionService(house).resolve(
        claim.id,
        Resolution.PRICE_ADJUSTMENT,
        amount=Money.parse("600.00"),
        reason="agreed with both sides",
    )
    posting.post_arbitration(claim.id)

    assert ledger.balance(LedgerAccount.FEE_INCOME) == Money.parse("580.00")
    assert ledger.balance(LedgerAccount.BUYER_RECEIVABLE, "d-200") == Money.parse("9945.00")
    assert ledger.is_balanced()


def test_a_title_arriving_branded_unwinds_a_finished_sale(
    house: sqlite3.Connection,
) -> None:
    run_id = run_to_the_block(house)
    sell(house, run_id)
    run = SaleRepository(house).run(run_id)
    PostingService(house).post_sale(run_id)
    PaymentService(house).receive("d-200", Money.parse("8545.00"))

    titles = TitleService(house)
    titles.expect(run.consignment_id)
    _, surprise = titles.receive(run.consignment_id, brand=TitleBrand.SALVAGE)
    assert surprise is TitleBrand.SALVAGE

    desk = ArbitrationDesk(house)
    claim = desk.open_claim(
        run_id,
        "d-200",
        DefectClass.BRANDING,
        "salvage title, nothing was called",
        at=SOLD_AT + timedelta(days=40),
    )
    desk.take_up(claim.id, "desk")
    outcome = ResolutionService(house).resolve(
        claim.id, Resolution.BUY_BACK, reason="undisclosed salvage brand"
    )
    assert outcome.unwinds
    assert outcome.buyer_receives == Money.parse("8200.00")


def test_the_sale_closes_and_recaps(house: sqlite3.Connection) -> None:
    run_id = run_to_the_block(house)
    sell(house, run_id)
    sales = SaleRepository(house)
    sales.close_sale("S-20240312")
    assert sales.get("S-20240312").status is SaleStatus.CLOSED

    recap = recap_report.build(house, "S-20240312")
    assert recap.sold == 1
    assert recap.gross == Money.parse("8200.00")
    assert recap.is_final
    assert "sale recap" in "\n".join(recap_report.render(house, "S-20240312"))


def test_a_unit_that_did_not_sell_goes_home_with_its_title(
    house: sqlite3.Connection,
) -> None:
    run_id = run_to_the_block(house)
    sales = SaleRepository(house)
    sales.open_sale("S-20240312")
    BiddingService(house).fall(run_id)
    run = sales.run(run_id)
    assert run.status is RunStatus.NO_SALE

    intake = IntakeService(house)
    consignment = intake.consignments.get(run.consignment_id)
    assert consignment.status is ConsignmentStatus.NO_SALE

    titles = TitleService(house)
    titles.expect(consignment.id)
    titles.receive(consignment.id)
    assert titles.release_to_seller(consignment.id).status.has_left
    assert intake.release(consignment.id).status is ConsignmentStatus.RELEASED


def test_the_building_verifies_clean_after_a_normal_night(
    house: sqlite3.Connection,
) -> None:
    """The end of a good night, asked of the system rather than of a person.

    Everything the verifier looks at should pass after a unit has gone all the
    way through, and this is the shortest statement of what "worked" means.
    """
    from autozest.verify import verify

    run_id = run_to_the_block(house)
    sell(house, run_id)
    run = SaleRepository(house).run(run_id)
    PostingService(house).post_sale(run_id)
    PaymentService(house).receive("d-200", Money.parse("8545.00"), reference="WIRE-1")
    titles = TitleService(house)
    titles.expect(run.consignment_id)
    titles.receive(run.consignment_id, issuing_state="TX")
    ProceedsService(house).release(run.consignment_id)
    titles.send(run.consignment_id)

    result = verify(house)
    assert result.is_usable, "\n".join(str(item) for item in result.failures)
    assert result.failures == ()


def test_the_verifier_notices_a_half_settled_night(house: sqlite3.Connection) -> None:
    """A unit sold and never posted is not a failure of anything the verifier
    checks, which is worth stating: settlement being behind is a work queue and
    not a fault, and the doctor is careful about the difference."""
    from autozest.verify import verify

    run_id = run_to_the_block(house)
    sell(house, run_id)
    result = verify(house)
    assert result.is_usable


def test_the_verifier_stops_on_a_ledger_that_does_not_balance(
    house: sqlite3.Connection,
) -> None:
    from autozest.verify import Level, verify

    house.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'lopsided', 'test', '2024-03-12T00:00:00+00:00')"
    )
    house.execute(
        "INSERT INTO ledger_line (entry_id, account, debit_cents) VALUES (1, 'cash', 100)"
    )
    result = verify(house)
    assert not result.is_usable
    assert all(item.level is Level.STOP for item in result.blocking)
