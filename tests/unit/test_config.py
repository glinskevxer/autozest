from datetime import date, time
from pathlib import Path

import pytest

from autozest.config import Settings, from_mapping, load
from autozest.errors import ConfigError

SAMPLE = """
auction_name = "Cedar Ridge Auto Auction"
auction_code = "CR07"
database = "cedar.sqlite3"
opens = "07:30:00"
closes = "18:00:00"
extra_closures = ["2024-05-06"]

[arbitration]
structural_window_days = 7

[fees]
floor = "45.00"
"""


def test_the_defaults_stand_on_their_own() -> None:
    settings = Settings()
    assert settings.auction_code == "AZ01"
    assert settings.calendar.opens == time(8, 0)
    assert settings.section("arbitration") == {}


def test_a_full_file_is_read_into_settings(tmp_path: Path) -> None:
    target = tmp_path / "autozest.toml"
    target.write_text(SAMPLE)
    settings = load(target)
    assert settings.auction_name == "Cedar Ridge Auto Auction"
    assert settings.database == "cedar.sqlite3"
    assert settings.opens == time(7, 30)
    assert settings.extra_closures == (date(2024, 5, 6),)
    assert settings.section("arbitration")["structural_window_days"] == 7
    assert settings.section("fees")["floor"] == "45.00"


def test_the_calendar_is_built_from_the_settings(tmp_path: Path) -> None:
    target = tmp_path / "autozest.toml"
    target.write_text(SAMPLE)
    calendar = load(target).calendar
    assert not calendar.is_business_day(date(2024, 5, 6))
    assert calendar.is_business_day(date(2024, 5, 7))
    assert calendar.hours_per_day == 10.5


def test_a_misspelt_setting_is_an_error_not_a_shrug() -> None:
    with pytest.raises(ConfigError, match="is not a setting"):
        from_mapping({"auction_nmae": "typo"})


def test_an_unknown_section_is_an_error_too() -> None:
    with pytest.raises(ConfigError, match="not a section this version knows") as caught:
        from_mapping({"arbitrtaion": {"window": 7}})
    assert "arbitration" in caught.value.context["known"]


def test_a_time_that_is_not_a_time() -> None:
    with pytest.raises(ConfigError, match="is not a time"):
        from_mapping({"opens": "half seven"})
    with pytest.raises(ConfigError, match="should be a time"):
        from_mapping({"opens": 730})


def test_dates_have_to_be_dates() -> None:
    with pytest.raises(ConfigError, match="should be a list of dates"):
        from_mapping({"extra_closures": "2024-05-06"})
    with pytest.raises(ConfigError, match="not a date"):
        from_mapping({"extra_closures": ["the sixth"]})
    with pytest.raises(ConfigError, match="holds a int"):
        from_mapping({"extra_closures": [20240506]})


def test_toml_native_dates_and_times_are_accepted_as_they_come(tmp_path: Path) -> None:
    target = tmp_path / "autozest.toml"
    target.write_text("opens = 07:30:00\nextra_openings = [2024-07-04]\n")
    settings = load(target)
    assert settings.opens == time(7, 30)
    assert settings.calendar.is_business_day(date(2024, 7, 4))


def test_text_settings_reject_a_number() -> None:
    with pytest.raises(ConfigError, match="should be text"):
        from_mapping({"auction_code": 7})


def test_closing_before_opening_is_caught_here_as_well() -> None:
    with pytest.raises(ConfigError, match="closes before it opens"):
        from_mapping({"opens": "18:00:00", "closes": "08:00:00"})


def test_a_missing_file_that_was_asked_for_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no configuration file"):
        load(tmp_path / "nowhere.toml")


def test_no_file_at_all_gives_the_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert load() == Settings()


def test_the_default_filename_is_picked_up_from_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "autozest.toml").write_text('auction_code = "ZZ99"\n')
    monkeypatch.chdir(tmp_path)
    assert load().auction_code == "ZZ99"


def test_broken_toml_says_so(tmp_path: Path) -> None:
    target = tmp_path / "autozest.toml"
    target.write_text("auction_name = 'unterminated\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load(target)


def test_a_relative_database_resolves_against_the_given_base(tmp_path: Path) -> None:
    settings = Settings(database="sale.sqlite3")
    assert settings.database_path(tmp_path) == tmp_path / "sale.sqlite3"


def test_an_absolute_database_is_left_alone(tmp_path: Path) -> None:
    settings = Settings(database=str(tmp_path / "elsewhere.sqlite3"))
    assert settings.database_path(Path("/nowhere")) == tmp_path / "elsewhere.sqlite3"
