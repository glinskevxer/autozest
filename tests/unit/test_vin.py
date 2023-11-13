import pytest

from autozest.vin import (
    Vin,
    VinError,
    check_digit,
    decode_model_year,
    is_valid,
    normalise,
    wmi_region,
)

ACCORD = "1HGCM82633A004352"
MODEL3 = "5YJ3E1EA0KF000316"
COROLLA = "JTDKN3DU8A0000001"
BMW = "WBA3A5C52DF000001"
SONATA = "KMHD35LEXEU000001"


def test_normalise_drops_the_separators_a_gate_pass_carries() -> None:
    assert normalise(" 1hg-cm826 33a0043 52 ") == ACCORD


def test_parse_accepts_a_real_vin_and_splits_it() -> None:
    vin = Vin.parse(ACCORD)
    assert vin.wmi == "1HG"
    assert vin.vds == "CM826"
    assert vin.check == "3"
    assert vin.year_code == "3"
    assert vin.plant == "A"
    assert vin.serial == "004352"
    assert str(vin) == ACCORD


def test_parse_normalises_before_validating() -> None:
    assert Vin.parse("1hg cm826-33a004352").value == ACCORD


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("1HGCM82633A00435", "17 characters"),
        ("1HGCM82633A0043521", "17 characters"),
        ("1HGCM8263IA004352", "characters a vin cannot hold"),
        ("1HGCM8263OA004352", "characters a vin cannot hold"),
        ("1HGCM8263QA004352", "characters a vin cannot hold"),
    ],
)
def test_parse_names_what_is_wrong_with_the_shape(text: str, fragment: str) -> None:
    with pytest.raises(VinError, match=fragment):
        Vin.parse(text)


def test_parse_rejects_a_bad_check_digit_and_says_what_it_wanted() -> None:
    broken = ACCORD[:8] + "9" + ACCORD[9:]
    with pytest.raises(VinError, match="should be '3' not '9'"):
        Vin.parse(broken)


def test_an_import_may_be_taken_without_its_check_digit() -> None:
    broken = ACCORD[:8] + "9" + ACCORD[9:]
    vin = Vin.parse(broken, require_check_digit=False)
    assert vin.check_digit_matches is False
    assert vin.value == broken


def test_check_digit_maps_a_remainder_of_ten_to_x() -> None:
    assert Vin.parse(SONATA).check == "X"
    assert check_digit(SONATA) == "X"


def test_check_digit_refuses_a_malformed_string() -> None:
    with pytest.raises(VinError, match="malformed"):
        check_digit("nope")


@pytest.mark.parametrize(
    ("vin", "year"),
    [(ACCORD, 2003), (MODEL3, 2019), (COROLLA, 2010), (BMW, 2013), (SONATA, 2014)],
)
def test_model_year_uses_position_seven_to_break_the_cycle(vin: str, year: int) -> None:
    assert Vin.parse(vin).model_year == year


def test_the_same_year_code_means_two_different_years() -> None:
    assert decode_model_year("A", "1") == 1980
    assert decode_model_year("A", "B") == 2010
    assert decode_model_year("Y", "1") == 2000
    assert decode_model_year("1", "1") == 2001


def test_an_unassigned_year_code_is_an_error() -> None:
    for code in ("I", "O", "Q", "U", "Z", "0"):
        with pytest.raises(VinError, match="model year code"):
            decode_model_year(code, "1")


@pytest.mark.parametrize(
    ("vin", "region"),
    [
        (ACCORD, "North America"),
        (COROLLA, "Asia"),
        (BMW, "Europe"),
        (SONATA, "Asia"),
    ],
)
def test_region_comes_off_the_first_character(vin: str, region: str) -> None:
    assert Vin.parse(vin).region == region


def test_region_of_an_unassigned_first_character() -> None:
    with pytest.raises(VinError, match="no assigned region"):
        wmi_region("0AA")


def test_last_eight_is_what_the_block_reads_out() -> None:
    vin = Vin.parse(ACCORD)
    assert vin.last_eight == "3A004352"
    assert vin.masked() == "*********3A004352"
    assert len(vin.masked()) == 17


def test_is_valid_is_the_quiet_form_of_parse() -> None:
    assert is_valid(ACCORD)
    assert is_valid(" 1hg cm826 33a004352 ")
    assert not is_valid(ACCORD[:8] + "9" + ACCORD[9:])
    assert not is_valid("")


def test_vins_compare_by_value() -> None:
    assert Vin.parse(ACCORD) == Vin.parse(ACCORD.lower())
    assert Vin.parse(ACCORD) != Vin.parse(MODEL3)
    assert len({Vin.parse(ACCORD), Vin.parse(ACCORD)}) == 1
