import pytest

from autozest.defects import DefectClass
from autozest.inspection.announcements import (
    REGISTRY,
    AnnouncementCode,
    Light,
    bars_class,
    forced_light,
    grants_class,
    labels,
    spec_for,
)


def test_every_code_is_in_the_registry() -> None:
    assert set(REGISTRY) == set(AnnouncementCode)
    for code in AnnouncementCode:
        assert spec_for(code).label


def test_the_labels_read_like_something_an_announcer_would_say() -> None:
    assert spec_for(AnnouncementCode.FRAME_DAMAGE).label == "frame damage"
    assert spec_for(AnnouncementCode.AS_IS).label == "as is, no arbitration"


def test_an_announced_defect_is_barred() -> None:
    called = {AnnouncementCode.FRAME_DAMAGE}
    assert bars_class(called, DefectClass.STRUCTURAL)
    assert not bars_class(called, DefectClass.ENGINE)


def test_as_is_bars_everything() -> None:
    called = {AnnouncementCode.AS_IS}
    for defect in DefectClass:
        assert bars_class(called, defect)


def test_nothing_called_bars_nothing() -> None:
    for defect in DefectClass:
        assert not bars_class(set(), defect)


def test_a_flood_title_covers_the_electrics_as_well_as_the_brand() -> None:
    called = {AnnouncementCode.FLOOD_TITLE}
    assert bars_class(called, DefectClass.BRANDING)
    assert bars_class(called, DefectClass.FLOOD)
    assert bars_class(called, DefectClass.ELECTRICAL)
    assert not bars_class(called, DefectClass.ENGINE)


def test_a_guarantee_grants_rather_than_bars() -> None:
    called = {AnnouncementCode.MECHANICALLY_GUARANTEED}
    assert grants_class(called, DefectClass.ENGINE)
    assert grants_class(called, DefectClass.TRANSMISSION)
    assert not grants_class(called, DefectClass.STRUCTURAL)
    assert not bars_class(called, DefectClass.ENGINE)


def test_lights_are_ranked_by_how_much_they_take_away() -> None:
    assert Light.GREEN.rank < Light.YELLOW.rank < Light.BLUE.rank < Light.RED.rank


def test_nothing_called_forces_no_light() -> None:
    assert forced_light(set()) is None
    assert forced_light({AnnouncementCode.PRIOR_TAXI}) is None


def test_as_is_forces_red() -> None:
    assert forced_light({AnnouncementCode.AS_IS}) is Light.RED


def test_frame_damage_forces_yellow_on_its_own() -> None:
    assert forced_light({AnnouncementCode.FRAME_DAMAGE}) is Light.YELLOW


def test_the_most_restrictive_forced_light_wins() -> None:
    called = {AnnouncementCode.FRAME_DAMAGE, AnnouncementCode.TITLE_ABSENT}
    assert forced_light(called) is Light.BLUE
    called.add(AnnouncementCode.FLOOD_DAMAGE)
    assert forced_light(called) is Light.RED


def test_a_missing_title_is_a_title_matter() -> None:
    assert spec_for(AnnouncementCode.TITLE_ABSENT).is_title_matter
    assert spec_for(AnnouncementCode.SALVAGE_TITLE).is_title_matter
    assert not spec_for(AnnouncementCode.NO_KEYS).is_title_matter


@pytest.mark.parametrize(
    "code",
    [
        AnnouncementCode.SALVAGE_TITLE,
        AnnouncementCode.REBUILT_TITLE,
        AnnouncementCode.FLOOD_TITLE,
        AnnouncementCode.LEMON_BUYBACK,
        AnnouncementCode.JUNK_TITLE,
    ],
)
def test_every_brand_bars_a_branding_claim(code: AnnouncementCode) -> None:
    assert bars_class({code}, DefectClass.BRANDING)


def test_salvage_and_rebuilt_run_under_different_lights() -> None:
    assert spec_for(AnnouncementCode.SALVAGE_TITLE).forces is Light.RED
    assert spec_for(AnnouncementCode.REBUILT_TITLE).forces is Light.YELLOW


def test_labels_come_back_in_a_stable_order() -> None:
    called = {AnnouncementCode.NO_KEYS, AnnouncementCode.FRAME_DAMAGE, AnnouncementCode.AS_IS}
    assert labels(called) == ["as is, no arbitration", "frame damage", "no keys"]
    assert labels(set()) == []
