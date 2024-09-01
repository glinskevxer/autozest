from datetime import date, datetime, timedelta

import pytest

from autozest.arbitration.desk import ArbitrationDesk
from autozest.block.bidding import BiddingService
from autozest.defects import DefectClass
from autozest.errors import Conflict, NotFound, PolicyViolation, StateError, ValidationError
from autozest.money import Money
from autozest.settlement.periods import Period, PeriodService, PeriodStatus
from autozest.settlement.postings import PostingService
from tests.conftest import Floor

MARCH = (date(2024, 3, 1), date(2024, 3, 31))
SOLD_AT = datetime(2024, 3, 12, 14, 30)


@pytest.fixture()
def periods(floor: Floor) -> PeriodService:
    return PeriodService(floor.conn)


def sold(floor: Floor) -> int:
    run_id = floor.catalogue(floor_price="3000.00")
    if floor.sales.get(floor.code).status.accepts_catalogue_changes:
        floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    floor.conn.execute("UPDATE run SET ran_at = ? WHERE id = ?", (SOLD_AT.isoformat(), run_id))
    return run_id


def test_a_period_that_ends_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="before it starts"):
        Period(id="P-1", starts_on=date(2024, 3, 31), ends_on=date(2024, 3, 1))


def test_opening_a_period(periods: PeriodService) -> None:
    period = periods.open_period(*MARCH)
    assert period.id == "P-202403"
    assert period.status is PeriodStatus.OPEN
    assert period.covers(date(2024, 3, 12))
    assert not period.covers(date(2024, 4, 1))


def test_two_periods_cannot_overlap(periods: PeriodService) -> None:
    periods.open_period(*MARCH)
    with pytest.raises(Conflict):
        periods.open_period(date(2024, 3, 15), date(2024, 4, 15))


def test_an_unknown_period(periods: PeriodService) -> None:
    with pytest.raises(NotFound):
        periods.get("P-199901")


def test_a_date_nobody_has_opened_a_period_for_still_posts(
    periods: PeriodService,
) -> None:
    periods.require_open(date(2025, 6, 1))


def test_an_open_period_takes_entries(periods: PeriodService) -> None:
    periods.open_period(*MARCH)
    periods.require_open(date(2024, 3, 12))


def test_a_closing_period_stops_taking_them(floor: Floor, periods: PeriodService) -> None:
    periods.open_period(*MARCH)
    periods.begin_close("P-202403")
    with pytest.raises(PolicyViolation) as caught:
        periods.require_open(date(2024, 3, 12))
    assert caught.value.rule == "period.closed"
    assert "post the correction in the period that is open" in str(caught.value)


def test_a_period_that_fails_its_checks_goes_back(floor: Floor, periods: PeriodService) -> None:
    sold(floor)
    periods.open_period(*MARCH)
    periods.begin_close("P-202403")
    failures = [check for check in periods.checks("P-202403") if not check.passed]
    assert failures
    assert "sold runs have no ledger entry" in str(failures[0])
    with pytest.raises(PolicyViolation) as caught:
        periods.close("P-202403", closed_by="m.byrne")
    assert caught.value.rule == "period.checks_failed"
    assert periods.reopen("P-202403").status is PeriodStatus.OPEN


def test_a_clean_period_closes(floor: Floor, periods: PeriodService) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    periods.open_period(*MARCH)
    periods.begin_close("P-202403")
    assert all(check.passed for check in periods.checks("P-202403"))
    closed = periods.close("P-202403", closed_by="m.byrne", note="nothing outstanding")
    assert closed.status is PeriodStatus.CLOSED
    assert closed.closed_by == "m.byrne"
    assert closed.closed_at is not None


def test_a_closed_period_stays_closed(floor: Floor, periods: PeriodService) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    periods.open_period(*MARCH)
    periods.begin_close("P-202403")
    periods.close("P-202403", closed_by="m.byrne")
    with pytest.raises(StateError, match="nothing"):
        periods.reopen("P-202403")


def test_closing_without_beginning_the_close(floor: Floor, periods: PeriodService) -> None:
    periods.open_period(*MARCH)
    with pytest.raises(StateError):
        periods.close("P-202403", closed_by="m.byrne")


def test_an_open_claim_stops_the_close(floor: Floor, periods: PeriodService) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    ArbitrationDesk(floor.conn).open_claim(
        run_id, "d-200", DefectClass.ENGINE, "knocking", at=SOLD_AT + timedelta(hours=1)
    )
    periods.open_period(*MARCH)
    periods.begin_close("P-202403")
    failed = [check for check in periods.checks("P-202403") if not check.passed]
    assert any("claims are still open" in check.detail for check in failed)


def test_the_period_totals(floor: Floor, periods: PeriodService) -> None:
    run_id = sold(floor)
    PostingService(floor.conn).post_sale(run_id)
    today = date.today()
    periods.open_period(today.replace(day=1), today)
    totals = periods.totals(f"P-{today:%Y%m}")
    assert totals["buyer_receivable"] == Money.parse("8545.00")


def test_the_period_list(periods: PeriodService) -> None:
    periods.open_period(*MARCH)
    periods.open_period(date(2024, 4, 1), date(2024, 4, 30))
    assert [p.id for p in periods.periods()] == ["P-202403", "P-202404"]
