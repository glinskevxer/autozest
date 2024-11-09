import sqlite3
from datetime import date

import pytest

from autozest.errors import NotFound, ValidationError
from autozest.title.branding import TitleBrand
from autozest.title.states import StateRule, TitleStates


@pytest.fixture()
def states(db: sqlite3.Connection) -> TitleStates:
    service = TitleStates(db)
    service.set_rule(StateRule(state="TX", duplicate_days=10, transfer_days=20))
    service.set_rule(
        StateRule(
            state="OK",
            duplicate_days=25,
            transfer_days=4,
            recognises_rebuilt=False,
            needs_inspection=True,
            note="wants a VIN inspection",
        )
    )
    return service


def test_a_state_has_to_be_two_letters() -> None:
    for bad in ("T", "TEX", "12"):
        with pytest.raises(ValidationError, match="two letter state"):
            StateRule(state=bad, duplicate_days=1, transfer_days=1)


def test_a_negative_turnaround() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        StateRule(state="TX", duplicate_days=-1, transfer_days=1)


def test_reading_a_rule_back(states: TitleStates) -> None:
    rule = states.rule("tx")
    assert rule.duplicate_days == 10
    assert rule.recognises_rebuilt
    assert not rule.needs_inspection


def test_an_unknown_state(states: TitleStates) -> None:
    with pytest.raises(NotFound):
        states.rule("ZZ")


def test_a_rule_can_be_moved(states: TitleStates) -> None:
    states.set_rule(StateRule(state="TX", duplicate_days=14, transfer_days=20))
    assert states.rule("TX").duplicate_days == 14
    assert len(states.rules()) == 2


def test_junk_is_titled_nowhere(states: TitleStates) -> None:
    assert not states.rule("TX").accepts(TitleBrand.JUNK)
    assert not states.rule("OK").accepts(TitleBrand.JUNK)


def test_a_state_that_does_not_recognise_rebuilt(states: TitleStates) -> None:
    assert states.rule("TX").accepts(TitleBrand.REBUILT)
    assert not states.rule("OK").accepts(TitleBrand.REBUILT)
    assert states.rule("OK").accepts(TitleBrand.CLEAN)


def test_turnarounds_are_counted_in_business_days(states: TitleStates) -> None:
    due = states.duplicate_due("TX", date(2024, 3, 12))
    assert due > date(2024, 3, 22)
    assert states.transfer_due("OK", date(2024, 3, 12)) == date(2024, 3, 18)


def test_a_unit_staying_in_state_gets_no_warnings(states: TitleStates) -> None:
    assert states.cross_border("TX", "TX", TitleBrand.REBUILT) == []


def test_a_rebuilt_unit_going_somewhere_that_will_not_take_it(
    states: TitleStates,
) -> None:
    warnings = states.cross_border("TX", "OK", TitleBrand.REBUILT)
    codes = [warning.code for warning in warnings]
    assert "brand_not_accepted" in codes
    assert "OK will not title a rebuilt unit" in str(warnings[0])


def test_a_state_that_wants_an_inspection_says_so(states: TitleStates) -> None:
    codes = [w.code for w in states.cross_border("TX", "OK", TitleBrand.CLEAN)]
    assert "inspection_needed" in codes
    assert "tight_transfer" in codes


def test_a_state_we_have_no_rules_for(states: TitleStates) -> None:
    warnings = states.cross_border("TX", "NM", TitleBrand.CLEAN)
    assert warnings[0].code == "unknown_state"
    assert "no rules on file for NM" in str(warnings[0])


def test_the_slow_states_come_out_first(states: TitleStates) -> None:
    assert [rule.state for rule in states.slowest()] == ["OK", "TX"]
    assert [rule.state for rule in states.slowest(1)] == ["OK"]
