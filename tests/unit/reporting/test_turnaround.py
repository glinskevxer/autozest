from datetime import datetime, timedelta

from autozest.arbitration.claims import Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.money import Money
from autozest.reporting import turnaround
from autozest.reporting.turnaround import Spread
from autozest.settlement.payments import PaymentService
from autozest.settlement.postings import PostingService
from autozest.title.service import TitleService
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


def sold(floor: Floor) -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    return run_id


def test_a_spread_with_nothing_in_it() -> None:
    spread = Spread("x", ())
    assert spread.count == 0
    assert spread.median == 0.0
    assert "nothing to measure" in str(spread)


def test_a_spread_describes_itself() -> None:
    spread = Spread("claim turnaround", (1, 3, 4, 40))
    assert spread.median == 3.5
    assert spread.best == 1
    assert spread.worst == 40
    assert spread.over(7) == 1
    assert "median 3.5 days" in str(spread)


def test_the_median_is_not_dragged_by_one_bad_one() -> None:
    spread = Spread("x", (2, 2, 3, 3, 60))
    assert spread.median == 3
    assert spread.worst == 60


def test_nothing_measured_on_an_empty_database(floor: Floor) -> None:
    for spread in turnaround.everything(floor.conn):
        assert spread.count == 0


def test_a_decided_claim_is_measured(floor: Floor) -> None:
    run_id = sold(floor)
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    ResolutionService(floor.conn).resolve(
        claim.id, Resolution.DENIED, reason="within tolerance"
    )
    assert turnaround.claim_turnaround(floor.conn).count == 1


def test_an_open_claim_is_not_measured(floor: Floor) -> None:
    run_id = sold(floor)
    ArbitrationDesk(floor.conn).open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    assert turnaround.claim_turnaround(floor.conn).count == 0


def test_first_touch_needs_somebody_to_have_touched_it(floor: Floor) -> None:
    run_id = sold(floor)
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    assert turnaround.claim_first_touch(floor.conn).count == 0
    desk.note(claim.id, "desk", "rang the seller")
    assert turnaround.claim_first_touch(floor.conn).count == 1


def test_a_title_arriving_and_going_out_are_measured_apart(floor: Floor) -> None:
    run_id = sold(floor)
    consignment_id = floor.sales.run(run_id).consignment_id
    PostingService(floor.conn).post_sale(run_id)
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    assert turnaround.title_arrival(floor.conn).count == 0
    titles.receive(consignment_id)
    assert turnaround.title_arrival(floor.conn).count == 1
    assert turnaround.title_despatch(floor.conn).count == 0
    titles.send(consignment_id)
    assert turnaround.title_despatch(floor.conn).count == 1


def test_the_rendered_report_names_all_four(floor: Floor) -> None:
    printed = "\n".join(turnaround.render(floor.conn))
    assert "claim first touch" in printed
    assert "claim turnaround" in printed
    assert "title arrival" in printed
    assert "title despatch" in printed
    assert "0 claims open" in printed
