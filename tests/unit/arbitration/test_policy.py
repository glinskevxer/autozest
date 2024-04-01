import sqlite3

import pytest

from autozest.arbitration.policy import (
    DEFAULT_RULES,
    POLICY_VERSION,
    ArbitrationPolicy,
    ClaimRule,
    classes_for,
    from_config,
)
from autozest.defects import DefectClass
from autozest.errors import ConfigError
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.storage.db import table_names
from autozest.timing import Window, WindowUnit


def test_the_arbitration_tables_are_there(db: sqlite3.Connection) -> None:
    for table in ("arbitration_claim", "claim_event", "post_sale_inspection"):
        assert table in table_names(db)


def test_every_defect_class_has_a_rule() -> None:
    policy = ArbitrationPolicy()
    for defect in DefectClass:
        assert policy.rule_for(defect) is not None


def test_a_mechanical_window_is_hours_and_a_paper_one_is_days() -> None:
    policy = ArbitrationPolicy()
    assert policy.window_for(DefectClass.TRANSMISSION).unit is WindowUnit.BUSINESS_HOURS
    assert policy.window_for(DefectClass.ODOMETER).unit is WindowUnit.CALENDAR_DAYS
    assert policy.window_for(DefectClass.STRUCTURAL).unit is WindowUnit.BUSINESS_DAYS


def test_cosmetic_damage_is_never_heard() -> None:
    policy = ArbitrationPolicy()
    assert not policy.rule_for(DefectClass.BODY_COSMETIC).is_arbitrable
    assert not policy.hears(Light.GREEN, DefectClass.BODY_COSMETIC)


def test_the_mechanical_classes_carry_a_money_threshold() -> None:
    policy = ArbitrationPolicy()
    assert policy.threshold_for(DefectClass.ENGINE) == Money.parse("3500.00")
    assert policy.threshold_for(DefectClass.STRUCTURAL) == Money.zero()


def test_green_hears_everything_that_is_arbitrable_at_all() -> None:
    assert classes_for(Light.GREEN) == frozenset(DefectClass)


def test_yellow_hears_only_what_the_lot_could_not_show() -> None:
    heard = classes_for(Light.YELLOW)
    assert DefectClass.ODOMETER in heard
    assert DefectClass.STRUCTURAL in heard
    assert DefectClass.ENGINE not in heard
    assert DefectClass.BODY_COSMETIC not in heard
    assert heard == {c for c in DefectClass if c.survives_inspection}


def test_blue_is_a_paperwork_lane() -> None:
    heard = classes_for(Light.BLUE)
    assert DefectClass.TITLE in heard
    assert DefectClass.ODOMETER in heard
    assert DefectClass.ENGINE not in heard


def test_red_still_has_to_deliver_a_good_title() -> None:
    heard = classes_for(Light.RED)
    assert heard == {DefectClass.TITLE, DefectClass.BRANDING}
    assert ArbitrationPolicy().hears(Light.RED, DefectClass.TITLE)
    assert not ArbitrationPolicy().hears(Light.RED, DefectClass.ENGINE)


def test_the_shipped_version_is_stamped() -> None:
    assert ArbitrationPolicy().version == POLICY_VERSION
    assert POLICY_VERSION.startswith("20")


def test_a_rule_with_no_window_is_not_arbitrable() -> None:
    assert not ClaimRule(Window(0, WindowUnit.CALENDAR_DAYS)).is_arbitrable
    assert ClaimRule(Window(1, WindowUnit.CALENDAR_DAYS)).is_arbitrable


def test_configuration_touches_only_what_it_names() -> None:
    policy = from_config({"version": "2025.1", "transmission": {"window": 72}})
    assert policy.version == "2025.1"
    assert policy.window_for(DefectClass.TRANSMISSION) == Window(72, WindowUnit.BUSINESS_HOURS)
    assert policy.window_for(DefectClass.ENGINE) == DEFAULT_RULES[DefectClass.ENGINE].window


def test_configuration_can_move_the_unit_as_well_as_the_number() -> None:
    policy = from_config({"engine": {"window": 5, "unit": "business_days"}})
    assert policy.window_for(DefectClass.ENGINE) == Window(5, WindowUnit.BUSINESS_DAYS)


def test_configuration_can_move_a_threshold() -> None:
    policy = from_config({"engine": {"threshold": "5000.00"}})
    assert policy.threshold_for(DefectClass.ENGINE) == Money.parse("5000.00")
    assert policy.window_for(DefectClass.ENGINE) == DEFAULT_RULES[DefectClass.ENGINE].window


def test_configuration_can_open_a_class_that_shipped_closed() -> None:
    policy = from_config({"body_cosmetic": {"window": 2, "unit": "business_days"}})
    assert policy.hears(Light.GREEN, DefectClass.BODY_COSMETIC)


def test_a_class_nobody_has_heard_of_is_refused() -> None:
    with pytest.raises(ConfigError, match="not a defect class"):
        from_config({"gearbox": {"window": 48}})


def test_a_window_unit_nobody_has_heard_of_is_refused() -> None:
    with pytest.raises(ConfigError, match="not a window unit"):
        from_config({"engine": {"unit": "fortnights"}})


def test_a_section_that_is_not_a_table_is_refused() -> None:
    with pytest.raises(ConfigError, match="should be a table"):
        from_config({"engine": 48})


def test_overlaying_onto_an_already_amended_policy() -> None:
    first = from_config({"engine": {"threshold": "5000.00"}})
    second = from_config({"engine": {"window": 24}}, first)
    assert second.threshold_for(DefectClass.ENGINE) == Money.parse("5000.00")
    assert second.window_for(DefectClass.ENGINE).amount == 24


def test_asking_for_a_rule_a_trimmed_policy_does_not_have() -> None:
    policy = ArbitrationPolicy(rules={DefectClass.TITLE: DEFAULT_RULES[DefectClass.TITLE]})
    with pytest.raises(ConfigError, match="has no rule for engine"):
        policy.rule_for(DefectClass.ENGINE)
