import pytest

from autozest.errors import ValidationError
from autozest.inspection.damage import (
    DamageKind,
    DamageLine,
    Panel,
    structural_lines,
    total_cost,
)
from autozest.money import Money


def line(
    panel: Panel, kind: DamageKind = DamageKind.DENT, severity: int = 3, **kwargs: object
) -> DamageLine:
    return DamageLine(panel=panel, kind=kind, severity=severity, **kwargs)  # type: ignore[arg-type]


def test_severity_has_to_be_on_the_scale() -> None:
    for severity in (0, 6, -1):
        with pytest.raises(ValidationError, match="outside 1 to 5"):
            line(Panel.HOOD, severity=severity)


def test_a_negative_estimate_is_refused() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        line(Panel.HOOD, estimate=Money(-1))


def test_structural_follows_the_panel_and_not_the_severity() -> None:
    assert line(Panel.APRON, DamageKind.CRACK, 2).is_structural
    assert not line(Panel.LEFT_FRONT_DOOR, DamageKind.CREASE, 5).is_structural


def test_the_structural_members_are_the_ones_that_hold_the_car_up() -> None:
    for panel in (Panel.RAIL, Panel.APRON, Panel.ROCKER, Panel.PILLAR, Panel.FLOOR_PAN):
        assert panel.is_structural_member
    for panel in (Panel.HOOD, Panel.ROOF, Panel.WHEEL, Panel.INTERIOR):
        assert not panel.is_structural_member


def test_the_cosmetic_panels_are_bolt_on_parts() -> None:
    assert Panel.FRONT_BUMPER.is_cosmetic_only
    assert Panel.HOOD.is_cosmetic_only
    assert not Panel.ROCKER.is_cosmetic_only
    assert not Panel.ROOF.is_cosmetic_only


def test_some_kinds_are_never_repaired() -> None:
    assert DamageKind.MISSING.needs_replacement
    assert DamageKind.TEAR.needs_replacement
    assert DamageKind.BENT.needs_replacement
    assert not DamageKind.SCRATCH.needs_replacement


def test_the_table_scales_with_severity() -> None:
    light = line(Panel.HOOD, DamageKind.DENT, 1).table_cost()
    middling = line(Panel.HOOD, DamageKind.DENT, 3).table_cost()
    heavy = line(Panel.HOOD, DamageKind.DENT, 5).table_cost()
    assert light < middling < heavy
    assert middling == Money(41_000)
    assert light == Money(10_250)


def test_rust_costs_more_than_a_dent_on_the_same_panel() -> None:
    dent = line(Panel.LEFT_QUARTER, DamageKind.DENT, 3).table_cost()
    rust = line(Panel.LEFT_QUARTER, DamageKind.RUST, 3).table_cost()
    assert rust > dent
    assert rust == Money(92_400)


def test_hail_is_cheaper_per_panel_than_a_dent() -> None:
    assert (
        line(Panel.ROOF, DamageKind.HAIL, 3).table_cost()
        < line(Panel.ROOF, DamageKind.DENT, 3).table_cost()
    )


def test_a_rail_costs_more_than_any_bolt_on_panel() -> None:
    rail = line(Panel.RAIL, DamageKind.BENT, 3).table_cost()
    bumper = line(Panel.FRONT_BUMPER, DamageKind.BENT, 3).table_cost()
    assert rail > bumper


def test_every_panel_has_a_price() -> None:
    for panel in Panel:
        assert line(panel).table_cost() > Money.zero()


def test_a_written_estimate_beats_the_table() -> None:
    written = line(Panel.HOOD, estimate=Money.parse("125.00"))
    assert written.cost == Money.parse("125.00")
    assert written.table_cost() == Money(41_000)


def test_a_zero_estimate_is_still_an_estimate() -> None:
    assert line(Panel.HOOD, estimate=Money.zero()).cost == Money.zero()


def test_the_report_adds_up() -> None:
    lines = [
        line(Panel.HOOD, DamageKind.DENT, 2),
        line(Panel.LEFT_QUARTER, DamageKind.SCRATCH, 1),
        line(Panel.WHEEL, DamageKind.CHIP, 1, estimate=Money.parse("40.00")),
    ]
    assert total_cost(lines) == Money(20_500 + 16_500 + 4_000)
    assert total_cost([]) == Money.zero()


def test_picking_out_the_structural_lines() -> None:
    lines = [
        line(Panel.HOOD, DamageKind.DENT, 4),
        line(Panel.RAIL, DamageKind.BENT, 2),
        line(Panel.APRON, DamageKind.CRACK, 1),
    ]
    found = structural_lines(lines)
    assert [f.panel for f in found] == [Panel.RAIL, Panel.APRON]


def test_a_line_describes_itself_in_words() -> None:
    assert line(Panel.LEFT_FRONT_DOOR, DamageKind.CREASE, 4).describe() == (
        "left front door crease severity 4"
    )
