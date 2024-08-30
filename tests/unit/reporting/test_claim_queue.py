from datetime import datetime, timedelta

import pytest

from autozest.arbitration.claims import ClaimStatus
from autozest.arbitration.desk import ArbitrationDesk
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.money import Money
from autozest.reporting import claims as claim_report
from autozest.reporting.claims import ESCALATION_DAYS
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def desk(floor: Floor) -> ArbitrationDesk:
    return ArbitrationDesk(floor.conn)


def sold(floor: Floor) -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    return run_id


def test_an_empty_desk(floor: Floor) -> None:
    queue = claim_report.build(floor.conn)
    assert queue.count == 0
    assert queue.escalations == ()
    assert queue.oldest() is None
    assert queue.exposure == Money.zero()


def test_a_fresh_claim_is_not_stale(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sold(floor)
    desk.open_claim(
        run_id,
        "d-200",
        DefectClass.ENGINE,
        "knocking",
        claimed=Money.parse("1800.00"),
        at=SOLD_AT + timedelta(hours=1),
    )
    queue = claim_report.build(floor.conn)
    assert queue.count == 1
    assert not queue.claims[0].needs_escalating
    assert queue.exposure == Money.parse("1800.00")


def test_an_untouched_claim_escalates(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sold(floor)
    desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    later = datetime.now().astimezone() + timedelta(days=20)
    queue = claim_report.build(floor.conn, as_at=later)
    assert queue.claims[0].idle_days >= ESCALATION_DAYS
    assert len(queue.escalations) == 1


def test_a_claim_that_is_being_worked_is_not_idle(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sold(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.note(claim.id, "desk", "rang the seller")
    queue = claim_report.build(floor.conn)
    assert queue.claims[0].last_action == "note"
    assert queue.claims[0].last_actor == "desk"
    assert queue.claims[0].idle_days == 0


def test_a_decided_claim_leaves_the_queue(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sold(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    assert claim_report.build(floor.conn).count == 1
    desk.claims.move(claim.id, ClaimStatus.DENIED, "desk")
    assert claim_report.build(floor.conn).count == 0


def test_the_statuses_are_counted(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sold(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    counts = claim_report.build(floor.conn).by_status()
    assert counts[ClaimStatus.UNDER_REVIEW] == 1
    assert ClaimStatus.OPEN not in counts


def test_the_rendered_queue_reads(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sold(floor)
    desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    text = "\n".join(claim_report.render(floor.conn))
    assert "arbitration queue" in text
    assert "1 open" in text
    assert "engine" in text
    assert "exposure claimed" in text


def test_a_claim_reported_in_the_past_is_not_negative(
    floor: Floor, desk: ArbitrationDesk
) -> None:
    run_id = sold(floor)
    desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    earlier = datetime.now().astimezone() - timedelta(days=30)
    assert claim_report.build(floor.conn, as_at=earlier).claims[0].open_days == 0
