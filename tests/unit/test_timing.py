from datetime import UTC, date, datetime, time

import pytest

from autozest.errors import ValidationError
from autozest.timing import (
    BusinessCalendar,
    Window,
    WindowUnit,
    business_days,
    holidays_for,
    observed_holidays,
)

CAL = BusinessCalendar()


def test_a_window_reads_as_english() -> None:
    assert str(Window(7, WindowUnit.BUSINESS_DAYS)) == "7 business days"
    assert str(Window(1, WindowUnit.BUSINESS_DAYS)) == "1 business day"
    assert str(Window(48, WindowUnit.BUSINESS_HOURS)) == "48 business hours"


def test_a_negative_window_is_refused() -> None:
    with pytest.raises(ValidationError):
        Window(-1, WindowUnit.CALENDAR_DAYS)


def test_the_fixed_holidays_move_off_the_weekend() -> None:
    assert date(2027, 12, 24) in holidays_for(2027)
    assert date(2021, 7, 5) in holidays_for(2021)
    assert date(2022, 12, 26) in holidays_for(2022)


def test_the_floating_holidays_land_where_they_should() -> None:
    year = holidays_for(2024)
    assert date(2024, 1, 15) in year
    assert date(2024, 2, 19) in year
    assert date(2024, 5, 27) in year
    assert date(2024, 9, 2) in year
    assert date(2024, 11, 28) in year


def test_the_day_after_thanksgiving_is_a_closure_here() -> None:
    assert date(2024, 11, 29) in holidays_for(2024)


def test_columbus_day_is_not_one_of_ours() -> None:
    assert date(2024, 10, 14) not in holidays_for(2024)
    assert CAL.is_business_day(date(2024, 10, 14))


def test_weekends_and_holidays_are_not_business_days() -> None:
    assert CAL.is_business_day(date(2024, 3, 12))
    assert not CAL.is_business_day(date(2024, 3, 16))
    assert not CAL.is_business_day(date(2024, 3, 17))
    assert not CAL.is_business_day(date(2024, 7, 4))


def test_a_local_closure_and_a_local_opening_both_apply() -> None:
    cal = BusinessCalendar(
        extra_closures=frozenset({date(2024, 3, 12)}),
        extra_openings=frozenset({date(2024, 3, 16)}),
    )
    assert not cal.is_business_day(date(2024, 3, 12))
    assert cal.is_business_day(date(2024, 3, 16))


def test_an_opening_beats_a_closure_on_the_same_day() -> None:
    day = date(2024, 7, 4)
    cal = BusinessCalendar(extra_openings=frozenset({day}))
    assert cal.is_business_day(day)


def test_stepping_over_a_weekend() -> None:
    assert CAL.next_business_day(date(2024, 3, 15)) == date(2024, 3, 18)
    assert CAL.previous_business_day(date(2024, 3, 18)) == date(2024, 3, 15)


def test_adding_business_days_skips_the_holiday_weekend() -> None:
    assert CAL.add_business_days(date(2024, 11, 27), 2) == date(2024, 12, 3)
    assert CAL.add_business_days(date(2024, 3, 12), 0) == date(2024, 3, 12)
    assert CAL.add_business_days(date(2024, 3, 12), 1) == date(2024, 3, 13)


def test_a_zero_window_lands_where_it_started_even_on_a_sunday() -> None:
    assert CAL.add_business_days(date(2024, 3, 17), 0) == date(2024, 3, 17)


def test_counting_backwards_is_a_different_call() -> None:
    with pytest.raises(ValidationError, match="count backwards"):
        CAL.add_business_days(date(2024, 3, 12), -1)


def test_business_days_between_counts_the_far_end_only() -> None:
    assert CAL.business_days_between(date(2024, 3, 12), date(2024, 3, 13)) == 1
    assert CAL.business_days_between(date(2024, 3, 15), date(2024, 3, 18)) == 1
    assert CAL.business_days_between(date(2024, 3, 12), date(2024, 3, 12)) == 0
    assert CAL.business_days_between(date(2024, 3, 18), date(2024, 3, 15)) == -1


def test_a_calendar_that_closes_before_it_opens_is_refused() -> None:
    with pytest.raises(ValidationError, match="closes before it opens"):
        BusinessCalendar(opens=time(17, 0), closes=time(8, 0))


def test_the_working_day_is_as_long_as_the_calendar_says() -> None:
    assert CAL.hours_per_day == 9.5
    assert BusinessCalendar(opens=time(9), closes=time(17)).hours_per_day == 8.0


def test_a_sale_that_ends_after_closing_starts_its_clock_next_morning() -> None:
    ended = datetime(2024, 3, 12, 21, 5)
    assert CAL.add_business_hours(ended, 0) == datetime(2024, 3, 13, 8, 0)
    assert CAL.add_business_hours(ended, 4) == datetime(2024, 3, 13, 12, 0)


def test_business_hours_roll_into_the_following_days() -> None:
    started = datetime(2024, 3, 12, 15, 30)
    assert CAL.add_business_hours(started, 2) == datetime(2024, 3, 12, 17, 30)
    assert CAL.add_business_hours(started, 3) == datetime(2024, 3, 13, 9, 0)
    assert CAL.add_business_hours(started, 12) == datetime(2024, 3, 14, 8, 30)


def test_forty_eight_business_hours_over_a_holiday_weekend() -> None:
    ended = datetime(2024, 11, 27, 16, 0)
    landed = CAL.add_business_hours(ended, 48)
    assert landed.date() >= date(2024, 12, 4)


def test_business_hours_only_run_forward() -> None:
    with pytest.raises(ValidationError, match="forward only"):
        CAL.add_business_hours(datetime(2024, 3, 12, 9, 0), -1)


def test_a_deadline_uses_the_unit_it_was_given() -> None:
    sold = datetime(2024, 3, 15, 14, 0)
    calendar_window = CAL.deadline(sold, Window(3, WindowUnit.CALENDAR_DAYS))
    business_window = CAL.deadline(sold, Window(3, WindowUnit.BUSINESS_DAYS))
    assert calendar_window == datetime(2024, 3, 18, 14, 0)
    assert business_window == datetime(2024, 3, 20, 17, 30)


def test_expiry_compares_against_the_deadline() -> None:
    sold = datetime(2024, 3, 15, 14, 0)
    window = Window(2, WindowUnit.BUSINESS_DAYS)
    assert not CAL.has_expired(sold, window, datetime(2024, 3, 19, 9, 0))
    assert CAL.has_expired(sold, window, datetime(2024, 3, 20, 9, 0))


def test_business_days_lists_the_open_days_in_a_range() -> None:
    days = business_days(date(2024, 3, 11), date(2024, 3, 17))
    assert days == [date(2024, 3, d) for d in (11, 12, 13, 14, 15)]


def test_observed_holidays_merges_and_sorts_the_years() -> None:
    days = observed_holidays([2024, 2025])
    assert days == sorted(days)
    assert date(2024, 12, 25) in days
    assert date(2025, 1, 1) in days


def test_a_deadline_keeps_the_timezone_it_was_given() -> None:
    aware = datetime(2024, 3, 12, 21, 5, tzinfo=UTC)
    for window in (
        Window(2, WindowUnit.BUSINESS_DAYS),
        Window(48, WindowUnit.BUSINESS_HOURS),
        Window(3, WindowUnit.CALENDAR_DAYS),
    ):
        landed = CAL.deadline(aware, window)
        assert landed.tzinfo is not None
        assert landed > aware


def test_a_naive_start_still_gives_a_naive_deadline() -> None:
    naive = datetime(2024, 3, 12, 21, 5)
    landed = CAL.deadline(naive, Window(48, WindowUnit.BUSINESS_HOURS))
    assert landed.tzinfo is None
