import pytest

from autozest.errors import ValidationError
from autozest.money import Money
from autozest.reporting.text import (
    Align,
    Column,
    Table,
    format_value,
    heading,
    indent,
    key_values,
    money_column,
    truncate,
    wrap,
)


def test_a_column_pads_to_its_width() -> None:
    assert Column("lane", 6).render("C") == "C     "
    assert Column("lane", 6, Align.RIGHT).render("C") == "     C"
    assert Column("lane", 6, Align.CENTRE).render("C") == "  C   "


def test_a_column_cannot_be_nothing_wide() -> None:
    with pytest.raises(ValidationError):
        Column("lane", 0)


def test_money_is_right_aligned_by_construction() -> None:
    column = money_column("hammer")
    assert column.align is Align.RIGHT
    assert column.render(Money.parse("12350.00")) == "   12,350.00"


def test_format_value_settles_each_kind_in_one_place() -> None:
    assert format_value(None) == ""
    assert format_value(True) == "yes"
    assert format_value(False) == "no"
    assert format_value(Money.parse("45.10")) == "45.10"
    assert format_value(214) == "214"


def test_truncation_marks_what_it_cut() -> None:
    assert truncate("Ridgeline Motors", 20) == "Ridgeline Motors"
    assert truncate("Ridgeline Motors", 10) == "Ridgelin.."
    assert truncate("Ridgeline", 2) == "Ri"
    with pytest.raises(ValidationError):
        truncate("x", 0)


def test_a_column_truncates_rather_than_pushing_the_table_out() -> None:
    assert Column("seller", 8).render("Ridgeline Motors") == "Ridgel.."


def test_a_table_lays_out_its_heading_and_rule() -> None:
    table = Table([Column("run", 5), Column("vin", 17), money_column("hammer")])
    assert table.width == 5 + 17 + 12 + 4
    assert table.heading().startswith("run  ")
    assert table.rule() == "-" * table.width
    assert table.rule("=") == "=" * table.width


def test_a_row_has_to_match_the_columns() -> None:
    table = Table([Column("run", 5), Column("vin", 17)])
    with pytest.raises(ValidationError, match="2 columns and the row has 1"):
        table.row(["C214"])


def test_rendering_rows() -> None:
    table = Table([Column("run", 5), money_column("hammer", 10)])
    lines = table.render([["C214", Money.parse("12350.00")], ["C215", Money.zero()]])
    assert len(lines) == 4
    assert lines[2] == "C214    12,350.00"
    assert lines[3].endswith("0.00")


def test_rendering_without_a_rule() -> None:
    table = Table([Column("run", 5)])
    assert len(table.render([["C214"]], ruled=False)) == 2


def test_a_table_needs_a_column() -> None:
    with pytest.raises(ValidationError, match="at least one column"):
        Table([])


def test_key_values_line_up_on_the_longest_label() -> None:
    lines = key_values([("consignment", "C-2024-000412"), ("floor", Money.parse("4500.00"))])
    assert lines[0] == "consignment  C-2024-000412"
    assert lines[1] == "floor        4,500.00"


def test_key_values_of_nothing() -> None:
    assert key_values([]) == []


def test_a_heading_gets_a_rule_the_length_of_its_title() -> None:
    assert heading("sale recap") == ["sale recap", "=" * len("sale recap")]


def test_wrapping_keeps_whole_words() -> None:
    text = "the buyer has forty eight business hours from the fall of the hammer"
    lines = wrap(text, 20)
    assert all(len(line) <= 20 for line in lines)
    assert " ".join(lines) == text


def test_wrapping_a_word_longer_than_the_width_leaves_it_alone() -> None:
    assert wrap("supercalifragilistic", 8) == ["supercalifragilistic"]


def test_wrapping_nothing() -> None:
    assert wrap("") == []
    with pytest.raises(ValidationError):
        wrap("x", 0)


def test_indent_leaves_blank_lines_blank() -> None:
    assert indent(["a", "", "b"], 2) == ["  a", "", "  b"]
