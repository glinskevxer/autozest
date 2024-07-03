from decimal import Decimal

import pytest

from autozest.errors import MoneyError
from autozest.money import Money, total


def test_from_dollars_accepts_str_int_and_decimal() -> None:
    assert Money.from_dollars("12350.00").cents == 1235000
    assert Money.from_dollars(12350).cents == 1235000
    assert Money.from_dollars(Decimal("0.07")).cents == 7


def test_from_dollars_rejects_floats_outright() -> None:
    with pytest.raises(MoneyError, match="floats are not accepted"):
        Money.from_dollars(12350.0)  # type: ignore[arg-type]


def test_from_dollars_rejects_sub_cent_precision() -> None:
    with pytest.raises(MoneyError, match="sub-cent"):
        Money.from_dollars("1.005")


def test_cents_must_be_an_int_and_bool_does_not_count() -> None:
    with pytest.raises(MoneyError):
        Money(True)


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("$12,350.00", 1235000),
        ("12350", 1235000),
        ("  -45.10 ", -4510),
        ("(45.10)", -4510),
        ("$0.07", 7),
        ("+8.5", 850),
        ("1,000,000.99", 100000099),
    ],
)
def test_parse_handles_the_shapes_that_arrive_in_imports(text: str, cents: int) -> None:
    assert Money.parse(text).cents == cents


@pytest.mark.parametrize("text", ["", "abc", "1.234", "12,34.00", "$", "(45.10", "1.2.3"])
def test_parse_rejects_junk(text: str) -> None:
    with pytest.raises(MoneyError):
        Money.parse(text)


def test_str_groups_thousands_and_keeps_two_places() -> None:
    assert str(Money(1235000)) == "12,350.00"
    assert str(Money(-4510)) == "-45.10"
    assert str(Money(7)) == "0.07"


def test_repr_round_trips_through_parse() -> None:
    amount = Money(-123456)
    assert eval(repr(amount)) == amount


def test_arithmetic_and_ordering() -> None:
    a, b = Money(1000), Money(250)
    assert a + b == Money(1250)
    assert a - b == Money(750)
    assert -b == Money(-250)
    assert abs(Money(-250)) == b
    assert b < a
    assert sorted([a, b]) == [b, a]
    assert not Money.zero()
    assert Money(-1).is_negative


def test_arithmetic_with_a_non_money_is_not_implemented() -> None:
    with pytest.raises(TypeError):
        Money(100) + 100
    with pytest.raises(TypeError):
        Money(100) - "45.00"


def test_times_rounds_half_away_from_zero_at_the_cent() -> None:
    assert Money(101).times(Decimal("0.5")) == Money(51)
    assert Money(-101).times(Decimal("0.5")) == Money(-51)
    assert Money(1235000).times(3) == Money(3705000)
    with pytest.raises(MoneyError):
        Money(100).times(0.5)  # type: ignore[arg-type]


def test_percent_reads_as_a_percentage_not_a_fraction() -> None:
    assert Money.parse("1000.00").percent("4.5") == Money.parse("45.00")
    assert Money.parse("1000.00").percent(10) == Money.parse("100.00")


def test_clamp_holds_between_bounds() -> None:
    low, high = Money(100), Money(500)
    assert Money(50).clamp(low, high) == low
    assert Money(900).clamp(low, high) == high
    assert Money(300).clamp(low, high) == Money(300)
    assert Money(50).clamp(high=high) == Money(50)
    with pytest.raises(MoneyError, match="inverted"):
        Money(300).clamp(high, low)


def test_allocate_parts_add_back_to_the_whole() -> None:
    parts = Money(10000).allocate([1, 1, 1])
    assert parts == [Money(3334), Money(3333), Money(3333)]
    assert total(parts) == Money(10000)


def test_allocate_is_weighted_not_equal() -> None:
    parts = Money(10000).allocate([70, 30])
    assert parts == [Money(7000), Money(3000)]


def test_allocate_keeps_the_sign_and_still_reconciles() -> None:
    parts = Money(-10000).allocate([1, 1, 1])
    assert total(parts) == Money(-10000)
    assert all(p.is_negative for p in parts)


def test_allocate_tolerates_a_zero_weight_beside_a_real_one() -> None:
    assert Money(100).allocate([0, 1]) == [Money.zero(), Money(100)]


@pytest.mark.parametrize("weights", [[], [0, 0], [-1, 2]])
def test_allocate_rejects_unusable_weights(weights: list[int]) -> None:
    with pytest.raises(MoneyError):
        Money(100).allocate(weights)


def test_split_puts_the_odd_cents_on_the_earlier_parts() -> None:
    assert Money(100).split(3) == [Money(34), Money(33), Money(33)]
    with pytest.raises(MoneyError):
        Money(100).split(0)


def test_total_of_nothing_is_zero() -> None:
    assert total([]) == Money.zero()


def test_an_amount_goes_into_an_f_string_with_an_alignment() -> None:
    amount = Money.parse("12350.00")
    assert f"{amount:>12}" == "   12,350.00"
    assert f"{amount:<12}|" == "12,350.00   |"
    assert f"{amount}" == "12,350.00"


def test_a_numeric_format_spec_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(MoneyError, match="number format"):
        f"{Money.parse('12350.00'):.2f}"
