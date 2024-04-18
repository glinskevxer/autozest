import sqlite3

import pytest

from autozest.inspection.announcements import AnnouncementCode
from autozest.storage.db import table_names
from autozest.title.branding import (
    DISCLOSURE,
    TitleBrand,
    brands_called,
    required_call,
    undisclosed_brand,
    was_disclosed,
)


def test_the_title_tables_are_there(db: sqlite3.Connection) -> None:
    assert "title_record" in table_names(db)
    assert "title_movement" in table_names(db)


def test_a_clean_title_needs_no_call() -> None:
    assert TitleBrand.CLEAN.is_clean
    assert required_call(TitleBrand.CLEAN) is None
    assert undisclosed_brand(TitleBrand.CLEAN, set()) is None


def test_every_brand_that_is_not_clean_has_a_call() -> None:
    for brand in TitleBrand:
        if not brand.is_clean:
            assert brand in DISCLOSURE


def test_junk_is_the_only_terminal_brand() -> None:
    assert TitleBrand.JUNK.is_terminal
    for brand in TitleBrand:
        if brand is not TitleBrand.JUNK:
            assert not brand.is_terminal


def test_a_bonded_title_can_still_be_retailed() -> None:
    assert not TitleBrand.BONDED.blocks_retail
    assert not TitleBrand.CLEAN.blocks_retail
    assert TitleBrand.SALVAGE.blocks_retail
    assert TitleBrand.FLOOD.blocks_retail


def test_a_brand_that_was_called_is_disclosed() -> None:
    assert was_disclosed(TitleBrand.SALVAGE, {AnnouncementCode.SALVAGE_TITLE})
    assert undisclosed_brand(TitleBrand.SALVAGE, {AnnouncementCode.SALVAGE_TITLE}) is None


def test_a_brand_that_was_not_called_is_reported() -> None:
    assert not was_disclosed(TitleBrand.SALVAGE, set())
    assert undisclosed_brand(TitleBrand.SALVAGE, set()) is TitleBrand.SALVAGE


def test_calling_the_wrong_brand_does_not_cover_it() -> None:
    assert not was_disclosed(TitleBrand.FLOOD, {AnnouncementCode.SALVAGE_TITLE})


def test_a_heavier_call_covers_a_lighter_brand() -> None:
    assert was_disclosed(TitleBrand.REBUILT, {AnnouncementCode.SALVAGE_TITLE})
    assert was_disclosed(TitleBrand.SALVAGE, {AnnouncementCode.JUNK_TITLE})


def test_a_lighter_call_does_not_cover_a_heavier_brand() -> None:
    assert not was_disclosed(TitleBrand.SALVAGE, {AnnouncementCode.REBUILT_TITLE})
    assert not was_disclosed(TitleBrand.JUNK, {AnnouncementCode.SALVAGE_TITLE})


def test_an_exempt_odometer_call_covers_a_mileage_brand() -> None:
    assert was_disclosed(TitleBrand.NOT_ACTUAL_MILEAGE, {AnnouncementCode.ODOMETER_EXEMPT})
    assert was_disclosed(
        TitleBrand.ODOMETER_DISCREPANCY, {AnnouncementCode.TRUE_MILEAGE_UNKNOWN}
    )


def test_as_is_does_not_cover_a_state_brand() -> None:
    assert not was_disclosed(TitleBrand.SALVAGE, {AnnouncementCode.AS_IS})
    assert undisclosed_brand(TitleBrand.FLOOD, {AnnouncementCode.AS_IS}) is TitleBrand.FLOOD


def test_as_is_does_not_come_close_to_covering_junk() -> None:
    assert not was_disclosed(TitleBrand.JUNK, {AnnouncementCode.AS_IS})


@pytest.mark.parametrize(
    ("brand", "code"),
    [
        (TitleBrand.SALVAGE, AnnouncementCode.SALVAGE_TITLE),
        (TitleBrand.REBUILT, AnnouncementCode.REBUILT_TITLE),
        (TitleBrand.FLOOD, AnnouncementCode.FLOOD_TITLE),
        (TitleBrand.LEMON, AnnouncementCode.LEMON_BUYBACK),
        (TitleBrand.JUNK, AnnouncementCode.JUNK_TITLE),
        (TitleBrand.BONDED, AnnouncementCode.BONDED_TITLE),
    ],
)
def test_each_brand_is_covered_by_its_own_call(
    brand: TitleBrand, code: AnnouncementCode
) -> None:
    assert was_disclosed(brand, {code})
    assert undisclosed_brand(brand, {code}) is None


def test_what_a_set_of_calls_covers() -> None:
    covered = brands_called({AnnouncementCode.SALVAGE_TITLE})
    assert TitleBrand.SALVAGE in covered
    assert TitleBrand.REBUILT in covered
    assert TitleBrand.FLOOD not in covered
    assert TitleBrand.CLEAN not in covered


def test_nothing_called_covers_nothing() -> None:
    assert brands_called(set()) == set()
