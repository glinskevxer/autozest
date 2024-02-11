from decimal import Decimal

import pytest

from autozest.errors import ValidationError
from autozest.inspection import grade as grading
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.inspection.grade import GradeBand
from autozest.money import Money


def line(panel: Panel, kind: DamageKind, severity: int, cost: str | None = None) -> DamageLine:
    return DamageLine(
        panel=panel,
        kind=kind,
        severity=severity,
        estimate=Money.parse(cost) if cost else None,
    )


def test_a_clean_unit_grades_at_the_top() -> None:
    result = grading.compute([])
    assert result.grade == Decimal("5.0")
    assert result.band is GradeBand.EXTRA_CLEAN
    assert not result.was_capped


def test_repairs_come_off_against_the_reference_value() -> None:
    lines = [line(Panel.HOOD, DamageKind.DENT, 3, "1500.00")]
    result = grading.compute(lines, reference_value=Money.parse("12000.00"))
    assert result.grade == Decimal("4.0")
    assert result.repair_total == Money.parse("1500.00")


def test_the_same_repair_grades_differently_on_a_dearer_unit() -> None:
    lines = [line(Panel.HOOD, DamageKind.DENT, 3, "2000.00")]
    cheap = grading.compute(lines, reference_value=Money.parse("8000.00"))
    dear = grading.compute(lines, reference_value=Money.parse("40000.00"))
    assert cheap.grade < dear.grade
    assert dear.grade == Decimal("4.6")


def test_the_grade_never_goes_below_zero() -> None:
    lines = [line(Panel.ROOF, DamageKind.CRACK, 5, "90000.00")]
    assert grading.compute(lines).grade == Decimal("0.0")


def test_structural_caps_the_grade_however_cheap_the_repair() -> None:
    lines = [line(Panel.RAIL, DamageKind.BENT, 1, "150.00")]
    result = grading.compute(lines)
    assert result.grade == Decimal("3.0")
    assert result.uncapped == Decimal("4.9")
    assert "structural damage" in result.caps_applied


def test_a_severe_cosmetic_panel_is_not_capped() -> None:
    lines = [line(Panel.LEFT_FRONT_DOOR, DamageKind.CREASE, 5, "150.00")]
    assert not grading.compute(lines).was_capped


def test_not_drivable_caps_lower_than_structural() -> None:
    result = grading.compute([], drivable=False)
    assert result.grade == Decimal("2.0")
    assert result.caps_applied == ("not drivable",)


def test_a_unit_that_will_not_start_caps_at_one() -> None:
    result = grading.compute([], drivable=False, engine_starts=False)
    assert result.grade == Decimal("1.0")
    assert result.band is GradeBand.ROUGH


def test_the_lowest_cap_wins_and_they_do_not_stack() -> None:
    lines = [line(Panel.RAIL, DamageKind.BENT, 2)]
    result = grading.compute(lines, drivable=False, engine_starts=False)
    assert result.grade == Decimal("1.0")
    assert len(result.caps_applied) == 3


def test_bald_tyres_cap_just_below_extra_clean() -> None:
    result = grading.compute([], tread_32nds=2)
    assert result.grade == Decimal("3.9")
    assert result.caps_applied == ("tyres at the wear bars",)


def test_legal_tread_is_not_a_cap() -> None:
    assert not grading.compute([], tread_32nds=8).was_capped


def test_a_cap_that_is_above_the_earned_grade_is_not_reported_as_applied() -> None:
    lines = [line(Panel.RAIL, DamageKind.BENT, 5, "6000.00")]
    result = grading.compute(lines)
    assert result.grade == Decimal("1.0")
    assert result.caps_applied == ()


@pytest.mark.parametrize(
    ("grade", "band"),
    [
        ("5.0", GradeBand.EXTRA_CLEAN),
        ("4.0", GradeBand.EXTRA_CLEAN),
        ("3.9", GradeBand.CLEAN),
        ("3.0", GradeBand.CLEAN),
        ("2.9", GradeBand.AVERAGE),
        ("1.0", GradeBand.ROUGH),
        ("0.9", GradeBand.SALVAGE),
        ("0.0", GradeBand.SALVAGE),
    ],
)
def test_the_bands_are_where_people_expect_them(grade: str, band: GradeBand) -> None:
    assert GradeBand.of(Decimal(grade)) is band


def test_the_explanation_says_what_capped_it() -> None:
    lines = [line(Panel.RAIL, DamageKind.BENT, 1, "150.00")]
    assert "capped by structural damage" in grading.compute(lines).explain()
    assert "of repairs" in grading.compute([]).explain()


def test_a_reference_value_of_nothing_is_refused() -> None:
    with pytest.raises(ValidationError, match="more than zero"):
        grading.compute([], reference_value=Money.zero())


def test_an_impossible_tread_depth_is_refused() -> None:
    with pytest.raises(ValidationError, match="not a tread depth"):
        grading.compute([], tread_32nds=13)
