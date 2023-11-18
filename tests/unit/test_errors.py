import pytest

from autozest.errors import (
    AutozestError,
    ConfigError,
    Conflict,
    MoneyError,
    NotFound,
    PolicyViolation,
    StateError,
    ValidationError,
    VinError,
    exit_status_for,
)


def test_every_domain_error_descends_from_the_base() -> None:
    for cls in (
        ValidationError,
        MoneyError,
        VinError,
        NotFound,
        Conflict,
        StateError,
        PolicyViolation,
        ConfigError,
    ):
        assert issubclass(cls, AutozestError)


def test_the_parsing_errors_are_validation_errors() -> None:
    assert issubclass(MoneyError, ValidationError)
    assert issubclass(VinError, ValidationError)


def test_codes_are_distinct_across_the_hierarchy() -> None:
    codes = [
        cls.code
        for cls in (
            ValidationError,
            MoneyError,
            VinError,
            NotFound,
            Conflict,
            StateError,
            PolicyViolation,
            ConfigError,
        )
    ]
    assert len(codes) == len(set(codes))


def test_str_without_context_is_just_the_message() -> None:
    assert str(Conflict("that run number is taken")) == "that run number is taken"


def test_str_appends_sorted_context() -> None:
    err = Conflict("that run number is taken", lane="C", run=214)
    assert str(err) == "that run number is taken (lane='C', run=214)"


def test_detail_puts_the_code_first_and_context_underneath() -> None:
    err = Conflict("that run number is taken", lane="C", run=214)
    assert err.detail() == "[conflict] that run number is taken\n    lane: 'C'\n    run: 214"


def test_not_found_builds_its_own_message() -> None:
    err = NotFound("consignment", "1HGCM82633A004352")
    assert err.kind == "consignment"
    assert err.key == "1HGCM82633A004352"
    assert "no consignment with key" in str(err)


def test_state_error_names_both_states() -> None:
    err = StateError("consignment", "sold", "checked_in")
    assert err.current == "sold"
    assert err.wanted == "checked_in"
    assert str(err).startswith("consignment is sold, this needs checked_in")


def test_policy_violation_always_names_its_rule() -> None:
    err = PolicyViolation("arb.window", "the claim is late", hours=193)
    assert err.rule == "arb.window"
    assert "rule='arb.window'" in str(err)
    assert err.context["hours"] == 193


def test_exit_statuses_separate_the_common_failures() -> None:
    assert exit_status_for(ValidationError("x")) == 2
    assert exit_status_for(ConfigError("x")) == 3
    assert exit_status_for(NotFound("sale", 1)) == 4
    assert exit_status_for(Conflict("x")) == 5
    assert exit_status_for(StateError("s", "a", "b")) == 6
    assert exit_status_for(PolicyViolation("r", "x")) == 7


def test_anything_else_exits_one() -> None:
    assert exit_status_for(RuntimeError("boom")) == 1
    assert exit_status_for(KeyboardInterrupt()) == 1


def test_the_errors_are_still_ordinary_exceptions() -> None:
    with pytest.raises(ValueError):
        raise MoneyError("floats are not accepted")
