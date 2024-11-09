from datetime import date, timedelta

import pytest

from autozest.errors import NotFound, PolicyViolation, ValidationError
from autozest.money import Money
from autozest.title.liens import Lienholder, LienService
from tests.conftest import Floor

QUOTED = date(2024, 3, 12)
GOOD_UNTIL = date(2024, 3, 22)


@pytest.fixture()
def liens(floor: Floor) -> LienService:
    service = LienService(floor.conn)
    service.add_lienholder(Lienholder(id="l-1", name="Ninth Street Finance"))
    return service


def consigned(floor: Floor) -> str:
    run_id = floor.catalogue(floor_price="3000.00")
    return floor.sales.run(run_id).consignment_id


def quote(liens: LienService, floor: Floor, **kwargs: object) -> str:
    defaults: dict[str, object] = {
        "amount": Money.parse("6000.00"),
        "good_until": GOOD_UNTIL,
        "per_diem": Money.parse("2.40"),
        "on": QUOTED,
    }
    defaults.update(kwargs)
    return liens.quote(consigned(floor), "l-1", **defaults).id  # type: ignore[arg-type]


def test_lienholders_come_back_sorted(liens: LienService) -> None:
    liens.add_lienholder(Lienholder(id="l-2", name="Apex Credit", contact="0100 1"))
    assert [holder.name for holder in liens.lienholders()] == [
        "Apex Credit",
        "Ninth Street Finance",
    ]


def test_recording_a_quote(liens: LienService, floor: Floor) -> None:
    reference = quote(liens, floor)
    found = liens.get(reference)
    assert found.amount == Money.parse("6000.00")
    assert found.per_diem == Money.parse("2.40")
    assert not found.is_paid
    assert found.is_current(GOOD_UNTIL)


def test_a_negative_payoff(liens: LienService, floor: Floor) -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        liens.quote(consigned(floor), "l-1", Money(-1), GOOD_UNTIL, on=QUOTED)


def test_a_quote_that_expires_before_it_was_given(liens: LienService, floor: Floor) -> None:
    with pytest.raises(ValidationError, match="expire before"):
        liens.quote(
            consigned(floor),
            "l-1",
            Money.parse("100.00"),
            QUOTED - timedelta(days=1),
            on=QUOTED,
        )


def test_an_unknown_quote(liens: LienService) -> None:
    with pytest.raises(NotFound):
        liens.get("Q-2024-999999")


def test_inside_the_window_the_figure_stands(liens: LienService, floor: Floor) -> None:
    found = liens.get(quote(liens, floor))
    assert found.amount_on(QUOTED) == Money.parse("6000.00")
    assert found.amount_on(GOOD_UNTIL) == Money.parse("6000.00")


def test_past_the_window_the_per_diem_runs(liens: LienService, floor: Floor) -> None:
    found = liens.get(quote(liens, floor))
    assert found.amount_on(GOOD_UNTIL + timedelta(days=5)) == Money.parse("6012.00")
    assert not found.is_current(GOOD_UNTIL + timedelta(days=1))


def test_a_quote_with_no_per_diem_does_not_move(liens: LienService, floor: Floor) -> None:
    found = liens.get(quote(liens, floor, per_diem=None))
    assert found.amount_on(GOOD_UNTIL + timedelta(days=90)) == Money.parse("6000.00")


def test_paying_in_full_clears_it(liens: LienService, floor: Floor) -> None:
    reference = quote(liens, floor)
    paid = liens.pay(reference, Money.parse("6000.00"), on=QUOTED + timedelta(days=2))
    assert paid.is_paid
    assert paid.paid == Money.parse("6000.00")
    assert liens.outstanding() == []


def test_a_short_payment_is_refused_rather_than_recorded(
    liens: LienService, floor: Floor
) -> None:
    reference = quote(liens, floor)
    with pytest.raises(PolicyViolation) as caught:
        liens.pay(reference, Money.parse("5900.00"), on=QUOTED)
    assert caught.value.rule == "lien.short"
    assert "100.00 short" in str(caught.value)


def test_paying_a_stale_quote_at_the_old_figure_is_short(
    liens: LienService, floor: Floor
) -> None:
    reference = quote(liens, floor)
    with pytest.raises(PolicyViolation) as caught:
        liens.pay(reference, Money.parse("6000.00"), on=GOOD_UNTIL + timedelta(days=5))
    assert "12.00 short" in str(caught.value)


def test_paying_twice(liens: LienService, floor: Floor) -> None:
    reference = quote(liens, floor)
    liens.pay(reference, Money.parse("6000.00"), on=QUOTED)
    with pytest.raises(PolicyViolation) as caught:
        liens.pay(reference, Money.parse("6000.00"), on=QUOTED)
    assert caught.value.rule == "lien.already_paid"


def test_the_stale_list_is_the_chase_list(liens: LienService, floor: Floor) -> None:
    quote(liens, floor)
    assert liens.stale(QUOTED) == []
    assert len(liens.stale(GOOD_UNTIL + timedelta(days=1))) == 1


def test_the_latest_quote_on_a_unit_wins(liens: LienService, floor: Floor) -> None:
    consignment_id = consigned(floor)
    liens.quote(consignment_id, "l-1", Money.parse("6000.00"), GOOD_UNTIL, on=QUOTED)
    liens.quote(
        consignment_id,
        "l-1",
        Money.parse("5800.00"),
        GOOD_UNTIL + timedelta(days=10),
        on=QUOTED + timedelta(days=5),
    )
    latest = liens.latest(consignment_id)
    assert latest is not None and latest.amount == Money.parse("5800.00")
