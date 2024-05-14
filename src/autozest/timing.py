"""Business days, business hours and the deadlines counted in them.

Almost every window in this system is counted from a moment on a sale night,
and none of them are counted in plain elapsed time. A buyer who takes delivery
on the Friday of a holiday weekend has not used up two days of a two day
arbitration window by Sunday evening, and a title that is due in twenty
business days is not late because the count ran through Thanksgiving.

The calendar is deliberately explicit rather than pulled from a package. The
auction observes the day after Thanksgiving and does not observe Columbus Day,
which no general purpose holiday list gets right for us.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from functools import lru_cache

from autozest.errors import ValidationError

SATURDAY = 5
SUNDAY = 6


class WindowUnit(Enum):
    """How a window counts down."""

    CALENDAR_DAYS = "calendar_days"
    BUSINESS_DAYS = "business_days"
    BUSINESS_HOURS = "business_hours"


@dataclass(frozen=True)
class Window:
    """A length of time, and the units it is counted in.

    Two windows of the same number are not interchangeable. Structural damage
    gets seven business days and a mechanical complaint gets forty eight
    business hours, and over a holiday weekend those are nothing like each
    other.
    """

    amount: int
    unit: WindowUnit

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValidationError(f"a window cannot be {self.amount} long")

    def __str__(self) -> str:
        noun = self.unit.value.replace("_", " ")
        if self.amount == 1:
            noun = noun[:-1]
        return f"{self.amount} {noun}"


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The nth given weekday of a month. Negative ``nth`` counts from the end."""
    if nth > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (nth - 1))
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset + 7 * (-nth - 1))


def _observed(day: date) -> date:
    """Move a fixed date holiday off a weekend the way the office does."""
    if day.weekday() == SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == SUNDAY:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=64)
def _holidays(year: int) -> frozenset[date]:
    """The closures for one year, worked out once.

    This is called for every day of every window walk, and a forty eight
    business hour deadline over a holiday week walks a lot of days. Building
    nine dates each time showed up as a third of the suite's runtime.
    """
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    return frozenset(
        {
            _observed(date(year, 1, 1)),
            _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3),
            _nth_weekday(year, 5, 0, -1),
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1),
            thanksgiving,
            thanksgiving + timedelta(days=1),
            _observed(date(year, 12, 25)),
        }
    )


def holidays_for(year: int) -> set[date]:
    """The days the auction is closed, for one calendar year."""
    return set(_holidays(year))


@dataclass(frozen=True)
class BusinessCalendar:
    """Which days count, and what hours of them count."""

    opens: time = time(8, 0)
    closes: time = time(17, 30)
    extra_closures: frozenset[date] = field(default_factory=frozenset)
    extra_openings: frozenset[date] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.opens >= self.closes:
            raise ValidationError("the calendar closes before it opens")

    @property
    def hours_per_day(self) -> float:
        opened = datetime.combine(date(2000, 1, 1), self.opens)
        closed = datetime.combine(date(2000, 1, 1), self.closes)
        return (closed - opened).total_seconds() / 3600

    def is_business_day(self, day: date) -> bool:
        if day in self.extra_openings:
            return True
        if day in self.extra_closures:
            return False
        if day.weekday() >= SATURDAY:
            return False
        return day not in _holidays(day.year)

    def next_business_day(self, day: date) -> date:
        cursor = day + timedelta(days=1)
        while not self.is_business_day(cursor):
            cursor += timedelta(days=1)
        return cursor

    def previous_business_day(self, day: date) -> date:
        cursor = day - timedelta(days=1)
        while not self.is_business_day(cursor):
            cursor -= timedelta(days=1)
        return cursor

    def add_business_days(self, start: date, days: int) -> date:
        """Count forward, skipping the days the auction is shut.

        Counting starts the day after ``start``. A window of zero business days
        lands on ``start`` itself even when that is a Sunday, because a zero
        window means the deadline has already passed.
        """
        if days < 0:
            raise ValidationError("use previous_business_day to count backwards")
        cursor = start
        for _ in range(days):
            cursor = self.next_business_day(cursor)
        return cursor

    def business_days_between(self, start: date, end: date) -> int:
        """How many business days lie in ``(start, end]``, negative if reversed."""
        if end < start:
            return -self.business_days_between(end, start)
        count = 0
        cursor = start
        while cursor < end:
            cursor += timedelta(days=1)
            if self.is_business_day(cursor):
                count += 1
        return count

    def add_business_hours(self, start: datetime, hours: int) -> datetime:
        """Count working hours forward from a moment.

        A moment outside opening hours is pulled to the next opening bell
        first, so a sale that ends at nine in the evening starts its forty eight
        hour clock at eight the next working morning rather than immediately.
        """
        if hours < 0:
            raise ValidationError("business hours count forward only")
        cursor = self._pull_to_open(start)
        remaining = float(hours)
        while remaining > 0:
            close_at = datetime.combine(cursor.date(), self.closes, cursor.tzinfo)
            available = (close_at - cursor).total_seconds() / 3600
            if remaining <= available:
                return cursor + timedelta(hours=remaining)
            remaining -= available
            cursor = datetime.combine(
                self.next_business_day(cursor.date()), self.opens, cursor.tzinfo
            )
        return cursor

    def _pull_to_open(self, moment: datetime) -> datetime:
        """Move a moment forward to the next time the doors are open.

        The result keeps whatever timezone it came in with. Everything stored
        in this system is timezone aware and everything a test writes by hand
        is not, and a deadline that silently changes which of those it is makes
        the comparison at the other end raise.
        """
        day = moment.date()
        if self.is_business_day(day):
            if moment.time() < self.opens:
                return datetime.combine(day, self.opens, moment.tzinfo)
            if moment.time() < self.closes:
                return moment
        return datetime.combine(self.next_business_day(day), self.opens, moment.tzinfo)

    def deadline(self, start: datetime, window: Window) -> datetime:
        """Apply a window to a starting moment."""
        if window.unit is WindowUnit.CALENDAR_DAYS:
            return start + timedelta(days=window.amount)
        if window.unit is WindowUnit.BUSINESS_DAYS:
            landed = self.add_business_days(start.date(), window.amount)
            return datetime.combine(landed, self.closes, start.tzinfo)
        return self.add_business_hours(start, window.amount)

    def has_expired(self, start: datetime, window: Window, now: datetime) -> bool:
        return now > self.deadline(start, window)


DEFAULT_CALENDAR = BusinessCalendar()


def business_days(
    start: date, end: date, calendar: BusinessCalendar | None = None
) -> list[date]:
    """Every business day in ``[start, end]``, for a report header."""
    cal = calendar or DEFAULT_CALENDAR
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cal.is_business_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def observed_holidays(years: Iterable[int]) -> list[date]:
    """Every closure across the given years, sorted."""
    found: set[date] = set()
    for year in years:
        found |= holidays_for(year)
    return sorted(found)
