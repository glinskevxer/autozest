from datetime import date

import pytest

from autozest.catalog.odometer import (
    MAX_PLAUSIBLE_MILES,
    OdometerStatus,
    Reading,
    check_declaration,
    derive_status,
    detect_rollback,
    exemption_years,
    is_exempt,
)
from autozest.errors import PolicyViolation, ValidationError

SALE = date(2024, 3, 12)


def r(miles: int, day: int = 1, source: str = "intake", digits: int = 6) -> Reading:
    return Reading(miles=miles, read_on=date(2024, 1, day), source=source, digits=digits)


def test_a_reading_rejects_what_cannot_be_a_reading() -> None:
    with pytest.raises(ValidationError, match="cannot read"):
        r(-1)
    with pytest.raises(ValidationError, match="believable"):
        r(MAX_PLAUSIBLE_MILES + 1)
    with pytest.raises(ValidationError, match="digits is not a thing"):
        r(100, digits=4)
    with pytest.raises(ValidationError, match="needs a source"):
        r(100, source="   ")


def test_the_mechanical_limit_follows_the_digit_count() -> None:
    assert r(1, digits=5).mechanical_limit == 99_999
    assert r(1, digits=6).mechanical_limit == 999_999
    assert r(99_999, digits=5).is_at_limit
    assert not r(99_998, digits=5).is_at_limit


def test_the_exemption_period_changed_length_at_model_year_2011() -> None:
    assert exemption_years(2009) == 10
    assert exemption_years(2010) == 10
    assert exemption_years(2011) == 20
    assert exemption_years(2024) == 20


def test_two_units_of_nearly_the_same_age_get_different_answers() -> None:
    assert is_exempt(2010, date(2020, 6, 1))
    assert not is_exempt(2011, date(2020, 6, 1))
    assert is_exempt(2011, date(2031, 6, 1))


def test_rollback_reports_the_largest_backwards_step() -> None:
    assert detect_rollback([r(80_000), r(82_000), r(90_000)]) is None
    assert detect_rollback([r(80_000), r(42_000)]) == 38_000
    assert detect_rollback([r(80_000), r(42_000), r(60_000)]) == 38_000
    assert detect_rollback([r(80_000), r(70_000), r(20_000)]) == 60_000


def test_rollback_measures_against_the_high_water_mark_not_the_last_reading() -> None:
    assert detect_rollback([r(90_000), r(10_000), r(20_000)]) == 80_000


def test_a_replaced_cluster_is_not_a_rollback() -> None:
    assert detect_rollback([r(180_000), r(12)], cluster_replaced=True) is None


def test_an_empty_history_has_no_rollback_and_no_status() -> None:
    assert detect_rollback([]) is None
    with pytest.raises(ValidationError, match="no readings"):
        derive_status([], 2019, SALE)


def test_a_clean_recent_unit_is_actual() -> None:
    assert derive_status([r(41_200), r(41_930)], 2019, SALE) is OdometerStatus.ACTUAL


def test_an_old_unit_is_exempt_before_anything_else_is_looked_at() -> None:
    assert derive_status([r(180_000)], 2004, SALE) is OdometerStatus.EXEMPT


def test_a_rolled_five_digit_cluster_exceeds_its_limits() -> None:
    status = derive_status([r(99_999, digits=5)], 2015, SALE)
    assert status is OdometerStatus.EXCEEDS_MECHANICAL_LIMITS


def test_a_rollback_beats_the_exemption() -> None:
    status = derive_status([r(180_000), r(90_000)], 1998, SALE)
    assert status is OdometerStatus.NOT_ACTUAL


def test_a_replaced_cluster_is_still_not_actual_mileage() -> None:
    status = derive_status([r(180_000), r(12)], 2019, SALE, cluster_replaced=True)
    assert status is OdometerStatus.NOT_ACTUAL


def test_a_seller_may_declare_something_worse_than_the_evidence() -> None:
    status = derive_status([r(41_200)], 2019, SALE, declared=OdometerStatus.NOT_ACTUAL)
    assert status is OdometerStatus.NOT_ACTUAL


def test_a_seller_declaring_something_softer_does_not_win() -> None:
    status = derive_status([r(180_000), r(90_000)], 2019, SALE, declared=OdometerStatus.ACTUAL)
    assert status is OdometerStatus.NOT_ACTUAL


def test_check_declaration_raises_on_the_softer_claim() -> None:
    with pytest.raises(PolicyViolation) as caught:
        check_declaration(OdometerStatus.ACTUAL, OdometerStatus.NOT_ACTUAL)
    assert caught.value.rule == "odometer.declaration"
    assert caught.value.context["derived"] == "not_actual"


def test_check_declaration_is_quiet_when_the_declaration_is_equal_or_worse() -> None:
    check_declaration(OdometerStatus.NOT_ACTUAL, OdometerStatus.NOT_ACTUAL)
    check_declaration(OdometerStatus.NOT_ACTUAL, OdometerStatus.ACTUAL)


def test_only_actual_avoids_a_disclosure() -> None:
    assert not OdometerStatus.ACTUAL.is_disclosable
    assert OdometerStatus.EXEMPT.is_disclosable
    assert OdometerStatus.NOT_ACTUAL.is_disclosable
    assert OdometerStatus.EXCEEDS_MECHANICAL_LIMITS.is_disclosable


def test_a_rolled_cluster_may_still_be_advertised_but_an_exempt_one_may_not() -> None:
    assert OdometerStatus.EXCEEDS_MECHANICAL_LIMITS.blocks_mileage_claim is False
    assert OdometerStatus.EXEMPT.blocks_mileage_claim is True
    assert OdometerStatus.NOT_ACTUAL.blocks_mileage_claim is True
