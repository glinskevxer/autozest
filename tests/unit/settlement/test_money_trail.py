"""Every movement of money leaves a line somebody can find.

These live in the services rather than in the command line, because money
moves from a terminal, from the nightly job and from an import, and a trail
that only knows about the first is a trail that answers the wrong question.
"""

from __future__ import annotations

import pytest

from autozest.audit import AuditLog
from autozest.block.bidding import BiddingService
from autozest.money import Money
from autozest.settlement.adjustments import AdjustmentService
from autozest.settlement.payments import Method, PaymentService
from autozest.settlement.postings import PostingService
from autozest.settlement.proceeds import ProceedsService
from autozest.title.service import TitleService
from tests.conftest import Floor


def sold_and_posted(floor: Floor) -> str:
    run_id = floor.catalogue(floor_price="3000.00")
    floor.sales.open_sale(floor.code)
    bidding = BiddingService(floor.conn)
    bidding.bid(run_id, "d-200", Money.parse("8200.00"))
    bidding.fall(run_id)
    PostingService(floor.conn).post_sale(run_id)
    return floor.sales.run(run_id).consignment_id


@pytest.fixture()
def trail(floor: Floor) -> AuditLog:
    return AuditLog(floor.conn)


def test_money_in_leaves_a_line(floor: Floor, trail: AuditLog) -> None:
    sold_and_posted(floor)
    PaymentService(floor.conn).receive(
        "d-200", Money.parse("8545.00"), method=Method.WIRE, reference="WIRE-1"
    )
    events = trail.for_subject("account", "d-200")
    assert [event.action for event in events] == ["payment.received"]
    assert "8,545.00 by wire" in (events[0].detail or "")


def test_money_out_leaves_a_different_line(floor: Floor, trail: AuditLog) -> None:
    sold_and_posted(floor)
    PaymentService(floor.conn).pay_out("d-100", Money.parse("1000.00"))
    events = trail.for_subject("account", "d-100")
    assert [event.action for event in events] == ["payment.paid"]


def test_an_adjustment_records_both_names(floor: Floor, trail: AuditLog) -> None:
    sold_and_posted(floor)
    AdjustmentService(floor.conn).credit_note(
        "d-200",
        Money.parse("400.00"),
        reason="goodwill",
        raised_by="p.dunne",
        approved_by="m.byrne",
    )
    events = trail.for_subject("account", "d-200")
    assert events[0].action == "adjustment.credit_note"
    assert "by p.dunne, approved by m.byrne" in (events[0].detail or "")


def test_a_small_adjustment_records_the_one_name(floor: Floor, trail: AuditLog) -> None:
    sold_and_posted(floor)
    AdjustmentService(floor.conn).credit_note(
        "d-200", Money.parse("40.00"), reason="fee twice", raised_by="p.dunne"
    )
    detail = trail.for_subject("account", "d-200")[0].detail or ""
    assert "by p.dunne" in detail
    assert "approved by" not in detail


def test_releasing_proceeds_records_what_the_seller_got(floor: Floor, trail: AuditLog) -> None:
    consignment_id = sold_and_posted(floor)
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    ProceedsService(floor.conn).release(consignment_id)
    events = trail.for_subject("consignment", consignment_id)
    assert events[-1].action == "proceeds.released"
    assert "7,965.00 to d-100" in (events[-1].detail or "")


def test_a_lien_payoff_shows_on_the_release_line(floor: Floor, trail: AuditLog) -> None:
    consignment_id = sold_and_posted(floor)
    PaymentService(floor.conn).receive("d-200", Money.parse("8545.00"))
    titles = TitleService(floor.conn)
    titles.expect(consignment_id)
    titles.receive(consignment_id)
    ProceedsService(floor.conn).release(
        consignment_id, lien=Money.parse("6000.00"), lienholder="d-300"
    )
    detail = trail.for_subject("consignment", consignment_id)[-1].detail or ""
    assert "after a 6,000.00 payoff" in detail


def test_nothing_is_recorded_when_nothing_moved(floor: Floor, trail: AuditLog) -> None:
    sold_and_posted(floor)
    assert trail.count() == 0
