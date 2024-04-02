from datetime import datetime, timedelta

import pytest

from autozest.arbitration.admissibility import ClaimContext, admissible_classes, assess
from autozest.arbitration.policy import ArbitrationPolicy, from_config
from autozest.defects import DefectClass
from autozest.inspection.announcements import AnnouncementCode, Light
from autozest.money import Money

SOLD = datetime(2024, 3, 12, 14, 30)
DEAR = Money.parse("12000.00")


def context(**kwargs: object) -> ClaimContext:
    defaults: dict[str, object] = {
        "defect": DefectClass.ENGINE,
        "light": Light.GREEN,
        "announcements": set(),
        "hammer": DEAR,
        "sold_at": SOLD,
        "claimed_at": SOLD + timedelta(hours=2),
    }
    defaults.update(kwargs)
    return ClaimContext(**defaults)  # type: ignore[arg-type]


def test_a_prompt_claim_on_a_green_unit_is_heard() -> None:
    ruling = assess(context())
    assert ruling.admissible
    assert ruling.deadline is not None
    assert "heard" in ruling.explain()


def test_an_announced_defect_is_never_heard() -> None:
    ruling = assess(context(announcements={AnnouncementCode.ENGINE_DEFECT}))
    assert not ruling.admissible
    assert ruling.rule == "arb.announced"


def test_as_is_bars_every_class_it_can() -> None:
    for defect in (DefectClass.ENGINE, DefectClass.STRUCTURAL, DefectClass.ODOMETER):
        ruling = assess(context(defect=defect, announcements={AnnouncementCode.AS_IS}))
        assert ruling.rule == "arb.announced"


def test_the_announcement_is_reported_before_the_window() -> None:
    ruling = assess(
        context(
            announcements={AnnouncementCode.ENGINE_DEFECT},
            claimed_at=SOLD + timedelta(days=400),
        )
    )
    assert ruling.rule == "arb.announced"


def test_a_yellow_unit_is_not_heard_on_a_gearbox() -> None:
    ruling = assess(context(defect=DefectClass.TRANSMISSION, light=Light.YELLOW))
    assert ruling.rule == "arb.light"


def test_a_yellow_unit_is_heard_on_a_rolled_odometer() -> None:
    assert assess(context(defect=DefectClass.ODOMETER, light=Light.YELLOW)).admissible


def test_a_red_unit_is_heard_on_the_title_and_nothing_else() -> None:
    assert assess(context(defect=DefectClass.TITLE, light=Light.RED)).admissible
    assert assess(context(defect=DefectClass.ENGINE, light=Light.RED)).rule == "arb.light"
    assert assess(context(defect=DefectClass.FLOOD, light=Light.RED)).rule == "arb.light"


def test_a_blue_unit_is_a_paperwork_lane() -> None:
    assert assess(context(defect=DefectClass.TITLE, light=Light.BLUE)).admissible
    assert assess(context(defect=DefectClass.STRUCTURAL, light=Light.BLUE)).rule == "arb.light"


def test_cosmetic_damage_is_not_an_arbitrable_class_at_all() -> None:
    ruling = assess(context(defect=DefectClass.BODY_COSMETIC))
    assert ruling.rule == "arb.not_arbitrable"


def test_a_cheap_unit_is_under_the_mechanical_threshold() -> None:
    ruling = assess(context(hammer=Money.parse("2000.00")))
    assert ruling.rule == "arb.threshold"
    assert "3,500.00" in (ruling.detail or "")


def test_a_cheap_unit_is_still_heard_on_a_title() -> None:
    assert assess(context(defect=DefectClass.TITLE, hammer=Money.parse("400.00"))).admissible


def test_the_window_closes() -> None:
    ruling = assess(context(claimed_at=SOLD + timedelta(days=30)))
    assert ruling.rule == "arb.window"
    assert "48 business hours" in (ruling.detail or "")


def test_an_odometer_claim_has_three_months() -> None:
    ruling = assess(context(defect=DefectClass.ODOMETER, claimed_at=SOLD + timedelta(days=80)))
    assert ruling.admissible


def test_the_same_class_is_not_heard_twice() -> None:
    ruling = assess(context(settled_classes=frozenset({DefectClass.ENGINE})))
    assert ruling.rule == "arb.repeat"


def test_a_different_class_on_the_same_unit_is_fine() -> None:
    assert assess(
        context(defect=DefectClass.STRUCTURAL, settled_classes=frozenset({DefectClass.ENGINE}))
    ).admissible


def test_a_guarantee_beats_the_announcement_for_the_classes_it_names() -> None:
    called = {AnnouncementCode.ENGINE_DEFECT, AnnouncementCode.MECHANICALLY_GUARANTEED}
    ruling = assess(context(announcements=called))
    assert ruling.admissible
    assert ruling.granted_by_guarantee
    assert "on the seller's guarantee" in ruling.explain()


def test_a_guarantee_beats_the_light_for_the_classes_it_names() -> None:
    called = {AnnouncementCode.MECHANICALLY_GUARANTEED}
    assert assess(
        context(defect=DefectClass.TRANSMISSION, light=Light.YELLOW, announcements=called)
    ).admissible


def test_a_guarantee_does_nothing_for_a_class_it_does_not_name() -> None:
    called = {AnnouncementCode.MECHANICALLY_GUARANTEED, AnnouncementCode.FRAME_DAMAGE}
    ruling = assess(context(defect=DefectClass.STRUCTURAL, announcements=called))
    assert ruling.rule == "arb.announced"


def test_a_guarantee_does_not_lift_the_money_threshold() -> None:
    called = {AnnouncementCode.MECHANICALLY_GUARANTEED}
    ruling = assess(context(announcements=called, hammer=Money.parse("2000.00")))
    assert ruling.rule == "arb.threshold"


def test_a_post_sale_inspection_stops_the_clock() -> None:
    late = SOLD + timedelta(days=10)
    without = assess(context(claimed_at=late))
    assert without.rule == "arb.window"
    with_psi = assess(
        context(
            claimed_at=late,
            inspection_ordered_at=SOLD + timedelta(hours=1),
            inspection_completed_at=SOLD + timedelta(days=5),
        )
    )
    assert with_psi.admissible


def test_an_unfinished_inspection_stops_the_clock_up_to_now() -> None:
    ruling = assess(
        context(
            claimed_at=SOLD + timedelta(days=10),
            inspection_ordered_at=SOLD + timedelta(hours=1),
        )
    )
    assert ruling.admissible


def test_an_inspection_does_nothing_for_a_title_claim() -> None:
    ruling = assess(
        context(
            defect=DefectClass.TITLE,
            claimed_at=SOLD + timedelta(days=60),
            inspection_ordered_at=SOLD + timedelta(hours=1),
            inspection_completed_at=SOLD + timedelta(days=50),
        )
    )
    assert ruling.rule == "arb.window"


def test_an_inspection_ordered_after_it_finished_is_ignored() -> None:
    ruling = assess(
        context(
            claimed_at=SOLD + timedelta(days=10),
            inspection_ordered_at=SOLD + timedelta(days=5),
            inspection_completed_at=SOLD + timedelta(hours=1),
        )
    )
    assert ruling.rule == "arb.window"


def test_an_amended_policy_changes_the_answer() -> None:
    late = context(claimed_at=SOLD + timedelta(days=10))
    assert not assess(late).admissible
    generous = from_config({"engine": {"window": 10, "unit": "business_days"}})
    assert assess(late, generous).admissible


def test_the_desk_can_ask_what_is_still_open() -> None:
    open_now = admissible_classes(context(light=Light.YELLOW))
    assert DefectClass.ODOMETER in open_now
    assert DefectClass.ENGINE not in open_now
    assert DefectClass.BODY_COSMETIC not in open_now


def test_nothing_is_open_on_a_red_unit_after_the_title_window() -> None:
    late = context(light=Light.RED, claimed_at=SOLD + timedelta(days=400))
    assert admissible_classes(late) == set()


@pytest.mark.parametrize("light", list(Light))
def test_a_guarantee_opens_the_drivetrain_under_every_light(light: Light) -> None:
    called = {AnnouncementCode.MECHANICALLY_GUARANTEED}
    policy = ArbitrationPolicy()
    ruling = assess(context(light=light, announcements=called), policy)
    assert ruling.admissible
