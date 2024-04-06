from datetime import datetime, timedelta

import pytest

from autozest.arbitration.claims import ClaimStatus
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.policy import from_config
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import PolicyViolation, ValidationError
from autozest.inspection.announcements import AnnouncementCode, Light
from autozest.inspection.service import InspectionService
from autozest.money import Money
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def desk(floor: Floor) -> ArbitrationDesk:
    return ArbitrationDesk(floor.conn)


def sell(
    floor: Floor,
    *,
    light: Light = Light.GREEN,
    price: str = "8200.00",
    announce: AnnouncementCode | None = None,
) -> int:
    """Put one unit through and have it sell, with the floor under the bid."""
    run_id = floor.catalogue(floor_price="100.00", light=light)
    run = floor.sales.run(run_id)
    if announce is not None:
        InspectionService(floor.conn).add_announcement(run.consignment_id, announce)
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    return run_id


def test_a_claim_on_an_unsold_unit(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = floor.catalogue()
    floor.sales.open_sale(floor.code)
    with pytest.raises(PolicyViolation) as caught:
        desk.open_claim(run_id, "d-200", DefectClass.ENGINE, "knocking")
    assert caught.value.rule == "arb.not_sold"


def test_only_the_buyer_of_record_may_claim(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    with pytest.raises(PolicyViolation) as caught:
        desk.open_claim(run_id, "d-100", DefectClass.ENGINE, "knocking")
    assert caught.value.rule == "arb.not_the_buyer"


def test_a_prompt_claim_is_taken(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    claim = desk.open_claim(
        run_id,
        "d-200",
        DefectClass.ENGINE,
        "knocking on a cold start",
        claimed=Money.parse("1800.00"),
        at=SOLD_AT + timedelta(hours=2),
    )
    assert claim.status is ClaimStatus.OPEN
    assert claim.policy_version == "2024.1"
    assert claim.claimed == Money.parse("1800.00")


def test_a_late_claim_is_refused_by_name(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    with pytest.raises(PolicyViolation) as caught:
        desk.open_claim(
            run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(days=30)
        )
    assert caught.value.rule == "arb.window"


def test_a_yellow_unit_refuses_a_gearbox_claim(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor, light=Light.YELLOW)
    with pytest.raises(PolicyViolation) as caught:
        desk.open_claim(
            run_id,
            "d-200",
            DefectClass.TRANSMISSION,
            "slipping",
            at=SOLD_AT + timedelta(hours=2),
        )
    assert caught.value.rule == "arb.light"


def test_an_announced_defect_is_refused(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor, announce=AnnouncementCode.ENGINE_DEFECT)
    with pytest.raises(PolicyViolation) as caught:
        desk.open_claim(
            run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=2)
        )
    assert caught.value.rule == "arb.announced"


def test_a_guarantee_is_noted_on_the_claim(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor, light=Light.YELLOW, announce=AnnouncementCode.MECHANICALLY_GUARANTEED)
    claim = desk.open_claim(
        run_id,
        "d-200",
        DefectClass.TRANSMISSION,
        "slipping",
        at=SOLD_AT + timedelta(hours=2),
    )
    kinds = [event.detail for event in desk.claims.events(claim.id)]
    assert "heard on the seller's guarantee" in kinds


def test_the_hammer_the_threshold_uses_is_the_price_at_the_fall(
    floor: Floor, desk: ArbitrationDesk
) -> None:
    run_id = sell(floor, price="3600.00")
    context = desk.context_for(run_id, DefectClass.ENGINE, SOLD_AT + timedelta(hours=1))
    assert context.hammer == Money.parse("3600.00")
    assert desk.assess(run_id, DefectClass.ENGINE, SOLD_AT + timedelta(hours=1)).admissible


def test_a_cheap_unit_is_under_the_mechanical_threshold(
    floor: Floor, desk: ArbitrationDesk
) -> None:
    run_id = sell(floor, price="3000.00")
    with pytest.raises(PolicyViolation) as caught:
        desk.open_claim(
            run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
        )
    assert caught.value.rule == "arb.threshold"


def test_the_same_class_cannot_be_brought_twice(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.take_up(claim.id, "desk")
    desk.claims.move(claim.id, ClaimStatus.DENIED, "desk")
    with pytest.raises(PolicyViolation) as caught:
        desk.open_claim(
            run_id,
            "d-200",
            DefectClass.ENGINE,
            "still knocking",
            at=SOLD_AT + timedelta(hours=2),
        )
    assert caught.value.rule == "arb.repeat"


def test_withdrawing_leaves_the_class_open(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.withdraw(claim.id, "desk", "seller offered to sort it")
    again = desk.open_claim(
        run_id,
        "d-200",
        DefectClass.ENGINE,
        "seller went quiet",
        at=SOLD_AT + timedelta(hours=2),
    )
    assert again.status is ClaimStatus.OPEN
    with pytest.raises(ValidationError):
        desk.withdraw(again.id, "desk", "  ")


def test_ordering_an_inspection_stops_the_clock(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    late = SOLD_AT + timedelta(days=10)
    assert not desk.assess(run_id, DefectClass.ENGINE, late).admissible
    desk.order_inspection(run_id, at=SOLD_AT + timedelta(hours=1))
    desk.complete_inspection(
        run_id,
        passed=False,
        findings="compression low on three",
        at=SOLD_AT + timedelta(days=5),
    )
    assert desk.assess(run_id, DefectClass.ENGINE, late).admissible


def test_an_inspection_is_ordered_once(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    desk.order_inspection(run_id, at=SOLD_AT + timedelta(hours=1))
    with pytest.raises(PolicyViolation) as caught:
        desk.order_inspection(run_id, at=SOLD_AT + timedelta(hours=2))
    assert caught.value.rule == "psi.already_ordered"


def test_completing_an_inspection_that_was_never_ordered(
    floor: Floor, desk: ArbitrationDesk
) -> None:
    run_id = sell(floor)
    with pytest.raises(PolicyViolation) as caught:
        desk.complete_inspection(run_id, passed=True)
    assert caught.value.rule == "psi.not_ordered"


def test_completing_one_twice(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    desk.order_inspection(run_id, at=SOLD_AT + timedelta(hours=1))
    desk.complete_inspection(run_id, passed=True, at=SOLD_AT + timedelta(days=1))
    with pytest.raises(PolicyViolation) as caught:
        desk.complete_inspection(run_id, passed=True, at=SOLD_AT + timedelta(days=2))
    assert caught.value.rule == "psi.already_done"


def test_the_inspection_record_carries_its_findings(
    floor: Floor, desk: ArbitrationDesk
) -> None:
    run_id = sell(floor)
    desk.order_inspection(run_id, at=SOLD_AT + timedelta(hours=1))
    check = desk.complete_inspection(
        run_id, passed=False, findings="rear main leak", at=SOLD_AT + timedelta(days=1)
    )
    assert check.passed is False
    assert check.findings == "rear main leak"
    assert not check.is_open


def test_the_desk_can_list_what_is_still_open(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor, light=Light.YELLOW)
    classes = desk.open_classes(run_id, SOLD_AT + timedelta(hours=1))
    assert DefectClass.ODOMETER in classes
    assert DefectClass.ENGINE not in classes


def test_an_amended_policy_reaches_the_desk(floor: Floor) -> None:
    generous = from_config({"engine": {"window": 15, "unit": "business_days"}})
    desk = ArbitrationDesk(floor.conn, generous)
    run_id = sell(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(days=10)
    )
    assert claim.policy_version == generous.version


def test_the_queue_and_the_last_touch(floor: Floor, desk: ArbitrationDesk) -> None:
    run_id = sell(floor)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    assert [c.id for c in desk.queue()] == [claim.id]
    desk.note(claim.id, "desk", "left a message with the seller")
    assert desk.touched_at(claim.id) is not None
    assert [e.kind for e in desk.claims.events(claim.id)] == ["opened", "note"]
