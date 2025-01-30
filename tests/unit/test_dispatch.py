from datetime import date, datetime, timedelta

import pytest

from autozest.arbitration.claims import Resolution
from autozest.arbitration.desk import ArbitrationDesk
from autozest.arbitration.resolution import ResolutionService
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.dispatch import CHASE_AFTER_DAYS, DispatchService
from autozest.money import Money
from autozest.notices import NoticeQueue
from autozest.settlement.postings import PostingService
from autozest.title.service import TitleService
from tests.conftest import SALE_DAY, Floor

SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def dispatch(floor: Floor) -> DispatchService:
    return DispatchService(floor.conn)


def sold_and_posted(floor: Floor) -> str:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    PostingService(floor.conn).post_sale(run_id)
    return floor.sales.run(run_id).consignment_id


def test_nothing_to_say_on_an_empty_database(dispatch: DispatchService) -> None:
    result = dispatch.run()
    assert result.queued == 0
    assert result.already_there == 0


def test_a_buyer_who_owes_gets_a_letter(floor: Floor, dispatch: DispatchService) -> None:
    sold_and_posted(floor)
    result = dispatch.run(as_at=SALE_DAY)
    assert result.kinds["payment.due"] == 1
    queued = NoticeQueue(floor.conn).pending()
    assert any("8,545.00" in notice.subject for notice in queued)


def test_a_title_is_not_chased_too_early(floor: Floor, dispatch: DispatchService) -> None:
    consignment_id = sold_and_posted(floor)
    TitleService(floor.conn).expect(consignment_id)
    result = dispatch.run(as_at=SALE_DAY + timedelta(days=3))
    assert "title.chase" not in result.kinds


def test_a_title_outstanding_long_enough_is_chased(
    floor: Floor, dispatch: DispatchService
) -> None:
    consignment_id = sold_and_posted(floor)
    TitleService(floor.conn).expect(consignment_id)
    result = dispatch.run(as_at=SALE_DAY + timedelta(days=CHASE_AFTER_DAYS + 1))
    assert result.kinds["title.chase"] == 1


def test_running_twice_on_the_same_day_says_nothing_twice(
    floor: Floor, dispatch: DispatchService
) -> None:
    consignment_id = sold_and_posted(floor)
    TitleService(floor.conn).expect(consignment_id)
    day = SALE_DAY + timedelta(days=CHASE_AFTER_DAYS + 1)
    first = dispatch.run(as_at=day)
    second = dispatch.run(as_at=day)
    assert first.queued > 0
    assert second.queued == 0
    assert second.already_there == first.queued


def test_a_later_week_chases_again(floor: Floor, dispatch: DispatchService) -> None:
    consignment_id = sold_and_posted(floor)
    TitleService(floor.conn).expect(consignment_id)
    dispatch.run(as_at=SALE_DAY + timedelta(days=CHASE_AFTER_DAYS + 1))
    later = dispatch.run(as_at=SALE_DAY + timedelta(days=CHASE_AFTER_DAYS + 9))
    assert later.kinds["title.chase"] == 1


def test_a_title_that_arrived_stops_being_chased(
    floor: Floor, dispatch: DispatchService
) -> None:
    consignment_id = sold_and_posted(floor)
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    result = dispatch.run(as_at=SALE_DAY + timedelta(days=60))
    assert "title.chase" not in result.kinds


def test_a_decided_claim_gets_a_letter(floor: Floor, dispatch: DispatchService) -> None:
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    desk = ArbitrationDesk(floor.conn)
    claim = desk.open_claim(
        run_id,
        "d-200",
        DefectClass.ENGINE,
        "knocking",
        at=SOLD_AT + timedelta(hours=1),
    )
    desk.take_up(claim.id, "desk")
    ResolutionService(floor.conn).resolve(
        claim.id, Resolution.DENIED, reason="within tolerance"
    )
    result = dispatch.run(as_at=SALE_DAY)
    assert result.kinds["claim.decided"] == 1


def test_the_next_chase_date(dispatch: DispatchService) -> None:
    assert dispatch.next_chase("C-1", date(2024, 3, 12)) == date(2024, 3, 26)


def test_the_summary_reads(floor: Floor, dispatch: DispatchService) -> None:
    sold_and_posted(floor)
    assert "queued" in dispatch.run(as_at=SALE_DAY).summary()
