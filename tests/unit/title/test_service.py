from datetime import date, datetime, timedelta

import pytest

from autozest.arbitration.desk import ArbitrationDesk
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import Conflict, NotFound, PolicyViolation, StateError
from autozest.inspection.announcements import AnnouncementCode
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.title.branding import TitleBrand
from autozest.title.service import TitleService, TitleStatus
from tests.conftest import Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def titles(floor: Floor) -> TitleService:
    return TitleService(floor.conn)


def catalogued(floor: Floor, announce: AnnouncementCode | None = None) -> tuple[str, int]:
    run_id = floor.catalogue(floor_price="3000.00")
    run = floor.sales.run(run_id)
    if announce is not None:
        InspectionService(floor.conn).add_announcement(run.consignment_id, announce)
    return run.consignment_id, run_id


def sell(floor: Floor, run_id: int, price: str = "8200.00") -> None:
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse(price))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))


def test_opening_a_title_record(floor: Floor, titles: TitleService) -> None:
    consignment_id, _ = catalogued(floor)
    record = titles.expect(consignment_id)
    assert record.status is TitleStatus.EXPECTED
    assert record.brand is TitleBrand.CLEAN
    assert [m.to_status for m in titles.movements(consignment_id)] == [TitleStatus.EXPECTED]


def test_a_second_record_for_the_same_unit(floor: Floor, titles: TitleService) -> None:
    consignment_id, _ = catalogued(floor)
    titles.expect(consignment_id)
    with pytest.raises(Conflict):
        titles.expect(consignment_id)


def test_a_unit_with_no_record(floor: Floor, titles: TitleService) -> None:
    consignment_id, _ = catalogued(floor)
    assert titles.find(consignment_id) is None
    with pytest.raises(NotFound):
        titles.require(consignment_id)


def test_receiving_a_clean_title(floor: Floor, titles: TitleService) -> None:
    consignment_id, _ = catalogued(floor)
    titles.expect(consignment_id)
    record, surprise = titles.receive(
        consignment_id, issuing_state="TX", title_number="TX99182", on=date(2024, 3, 14)
    )
    assert record.status is TitleStatus.ON_HAND
    assert record.issuing_state == "TX"
    assert record.received_on == date(2024, 3, 14)
    assert surprise is None


def test_a_title_arriving_with_a_brand_nobody_called(
    floor: Floor, titles: TitleService
) -> None:
    consignment_id, run_id = catalogued(floor)
    sell(floor, run_id)
    titles.expect(consignment_id)
    _, surprise = titles.receive(consignment_id, brand=TitleBrand.SALVAGE)
    assert surprise is TitleBrand.SALVAGE
    details = [m.detail for m in titles.movements(consignment_id)]
    assert any(detail and "the sale unwinds" in detail for detail in details)


def test_a_brand_that_was_called_is_no_surprise(floor: Floor, titles: TitleService) -> None:
    consignment_id, run_id = catalogued(floor, AnnouncementCode.SALVAGE_TITLE)
    sell(floor, run_id)
    titles.expect(consignment_id)
    _, surprise = titles.receive(consignment_id, brand=TitleBrand.SALVAGE)
    assert surprise is None


def test_a_title_cannot_be_received_twice(floor: Floor, titles: TitleService) -> None:
    consignment_id, _ = catalogued(floor)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    with pytest.raises(StateError):
        titles.receive(consignment_id)


def test_the_attorney_lane_and_back(floor: Floor, titles: TitleService) -> None:
    consignment_id, _ = catalogued(floor)
    titles.expect(consignment_id)
    titles.mark(consignment_id, TitleStatus.ATTORNEY, detail="seller chasing a duplicate")
    assert titles.get(consignment_id).status is TitleStatus.ATTORNEY
    titles.receive(consignment_id)
    assert titles.get(consignment_id).status is TitleStatus.ON_HAND


def test_a_sent_title_goes_nowhere(floor: Floor, titles: TitleService) -> None:
    consignment_id, run_id = catalogued(floor)
    sell(floor, run_id)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    titles.send(consignment_id)
    with pytest.raises(StateError, match="nothing"):
        titles.mark(consignment_id, TitleStatus.RELEASED)


def test_a_title_is_held_until_it_is_here(floor: Floor, titles: TitleService) -> None:
    consignment_id, run_id = catalogued(floor)
    sell(floor, run_id)
    titles.expect(consignment_id)
    assert "the title is expected" in titles.holds(consignment_id)
    with pytest.raises(PolicyViolation) as caught:
        titles.send(consignment_id)
    assert caught.value.rule == "title.held"


def test_a_title_is_held_while_the_unit_has_not_sold(
    floor: Floor, titles: TitleService
) -> None:
    consignment_id, _ = catalogued(floor)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    assert "the unit has not sold" in titles.holds(consignment_id)


def test_a_title_is_held_while_a_claim_is_open(floor: Floor, titles: TitleService) -> None:
    consignment_id, run_id = catalogued(floor)
    sell(floor, run_id)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    assert titles.holds(consignment_id) == []
    desk = ArbitrationDesk(floor.conn)
    desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    assert "1 arbitration claims are open" in titles.holds(consignment_id)


def test_a_decided_claim_stops_holding_the_title(floor: Floor, titles: TitleService) -> None:
    consignment_id, run_id = catalogued(floor)
    sell(floor, run_id)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    desk.withdraw(claim.id, "desk", "sorted privately")
    assert titles.holds(consignment_id) == []
    assert titles.send(consignment_id).status is TitleStatus.SENT


def test_a_title_going_back_to_the_seller(floor: Floor, titles: TitleService) -> None:
    consignment_id, _ = catalogued(floor)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    released = titles.release_to_seller(consignment_id)
    assert released.status is TitleStatus.RELEASED
    assert released.status.has_left


def test_a_sold_units_title_does_not_go_back_to_the_seller(
    floor: Floor, titles: TitleService
) -> None:
    consignment_id, run_id = catalogued(floor)
    sell(floor, run_id)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    with pytest.raises(PolicyViolation) as caught:
        titles.release_to_seller(consignment_id)
    assert caught.value.rule == "title.sold"


def test_the_outstanding_list_empties_as_titles_arrive(
    floor: Floor, titles: TitleService
) -> None:
    first, _ = catalogued(floor)
    second, _ = catalogued(floor)
    titles.expect(first)
    titles.expect(second)
    assert len(titles.outstanding()) == 2
    titles.receive(first)
    assert [record.consignment_id for record in titles.outstanding()] == [second]


def test_the_movement_log_reads_in_order(floor: Floor, titles: TitleService) -> None:
    consignment_id, run_id = catalogued(floor)
    sell(floor, run_id)
    titles.expect(consignment_id)
    titles.receive(consignment_id, actor="c.mott")
    titles.send(consignment_id, actor="c.mott")
    statuses = [m.to_status for m in titles.movements(consignment_id)]
    assert statuses == [TitleStatus.EXPECTED, TitleStatus.ON_HAND, TitleStatus.SENT]
    assert all(m.actor in ("title", "c.mott") for m in titles.movements(consignment_id))
