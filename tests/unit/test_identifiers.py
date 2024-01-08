from datetime import date

import pytest

from autozest.errors import ValidationError
from autozest.identifiers import (
    ALPHABET,
    consignment_id,
    gate_pass,
    gate_pass_sequence,
    parse_run_number,
    parse_sequenced,
    run_number,
    sale_code,
    sequenced,
)


def test_a_sequenced_reference_has_the_year_in_it() -> None:
    assert consignment_id(date(2024, 3, 12), 412) == "C-2024-000412"
    assert sequenced("A", date(2025, 1, 2), 1) == "A-2025-000001"


def test_a_reference_round_trips() -> None:
    assert parse_sequenced("C-2024-000412") == ("C", 2024, 412)
    assert parse_sequenced(" c-2024-000412 ") == ("C", 2024, 412)


@pytest.mark.parametrize("bad", ["", "C-24-000412", "C-2024-412", "CC-2024-000412", "nope"])
def test_junk_is_not_a_reference(bad: str) -> None:
    with pytest.raises(ValidationError, match="not a reference"):
        parse_sequenced(bad)


@pytest.mark.parametrize("prefix", ["c", "CC", "1", ""])
def test_the_prefix_has_to_be_one_upper_case_letter(prefix: str) -> None:
    with pytest.raises(ValidationError, match="prefix"):
        sequenced(prefix, date(2024, 1, 1), 1)


@pytest.mark.parametrize("sequence", [0, -1, 1_000_000])
def test_the_sequence_has_to_fit_six_digits(sequence: int) -> None:
    with pytest.raises(ValidationError, match="six digit"):
        sequenced("C", date(2024, 1, 1), sequence)


def test_the_alphabet_leaves_out_what_a_phone_call_ruins() -> None:
    for character in "IOSZ":
        assert character not in ALPHABET


def test_a_gate_pass_is_five_characters_and_reversible() -> None:
    for sequence in (0, 1, 31, 32, 4211, 33_554_431):
        text = gate_pass(sequence)
        assert len(text) == 5
        assert gate_pass_sequence(text) == sequence


def test_a_gate_pass_is_read_back_case_insensitively() -> None:
    assert gate_pass_sequence(gate_pass(4211).lower()) == 4211


def test_a_gate_pass_outside_the_range_is_refused() -> None:
    with pytest.raises(ValidationError, match="does not fit"):
        gate_pass(-1)
    with pytest.raises(ValidationError, match="does not fit"):
        gate_pass(len(ALPHABET) ** 5)


def test_reading_back_something_that_is_not_a_pass() -> None:
    with pytest.raises(ValidationError, match="five characters"):
        gate_pass_sequence("ABC")
    with pytest.raises(ValidationError, match="alphabet"):
        gate_pass_sequence("ABCIS")


def test_a_sale_is_named_for_its_day() -> None:
    assert sale_code(date(2024, 3, 12), 4) == "S-20240312"
    with pytest.raises(ValidationError, match="no lanes"):
        sale_code(date(2024, 3, 12), 0)


def test_a_run_number_is_a_lane_and_a_position() -> None:
    assert run_number("c", 214) == "C214"
    assert parse_run_number("c214") == ("C", 214)


@pytest.mark.parametrize("lane", ["", "CC", "1"])
def test_a_run_number_needs_one_lane_letter(lane: str) -> None:
    with pytest.raises(ValidationError, match="lane letter"):
        run_number(lane, 1)


@pytest.mark.parametrize("position", [0, 1000, -1])
def test_a_run_position_has_to_be_on_the_card(position: int) -> None:
    with pytest.raises(ValidationError, match="run position"):
        run_number("C", position)


@pytest.mark.parametrize("bad", ["C21", "CC214", "2141", "", "C2X4"])
def test_reading_back_something_that_is_not_a_run_number(bad: str) -> None:
    with pytest.raises(ValidationError, match="not a run number"):
        parse_run_number(bad)
