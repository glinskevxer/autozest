import sqlite3

import pytest

from autozest.consignment.models import (
    TRANSITIONS,
    Consignment,
    ConsignmentStatus,
    require_transition,
)
from autozest.errors import StateError, ValidationError
from autozest.money import Money
from autozest.storage.db import column_names, table_names


def consignment(**kwargs: object) -> Consignment:
    defaults: dict[str, object] = {
        "id": "C-2024-000412",
        "vin": "1HGCM82633A004352",
        "seller_id": "d-100",
    }
    defaults.update(kwargs)
    return Consignment(**defaults)  # type: ignore[arg-type]


def test_the_new_tables_are_there(db: sqlite3.Connection) -> None:
    for table in ("consignment", "condition_report", "damage_line", "announcement"):
        assert table in table_names(db)


def test_a_consignment_carries_a_floor_and_a_gate_pass(db: sqlite3.Connection) -> None:
    columns = column_names(db, "consignment")
    assert "floor_cents" in columns
    assert "gate_pass" in columns
    assert "title_expected" in columns


def test_a_consignment_needs_an_id() -> None:
    with pytest.raises(ValidationError, match="needs an id"):
        consignment(id="  ")


def test_an_unbelievable_key_count_is_refused() -> None:
    with pytest.raises(ValidationError, match="believable"):
        consignment(keys_count=9)


def test_a_negative_floor_is_refused() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        consignment(floor=Money(-1))


def test_the_open_statuses_are_the_ones_that_hold_the_vin() -> None:
    assert ConsignmentStatus.REGISTERED.is_open
    assert ConsignmentStatus.CATALOGUED.is_open
    assert ConsignmentStatus.NO_SALE.is_open
    assert not ConsignmentStatus.SOLD.is_open
    assert not ConsignmentStatus.WITHDRAWN.is_open
    assert not ConsignmentStatus.RELEASED.is_open


def test_a_registered_unit_is_not_on_the_ground_yet() -> None:
    assert not ConsignmentStatus.REGISTERED.is_on_the_ground
    assert ConsignmentStatus.ARRIVED.is_on_the_ground
    assert ConsignmentStatus.NO_SALE.is_on_the_ground
    assert not ConsignmentStatus.SOLD.is_on_the_ground


def test_the_normal_path_through_the_sale() -> None:
    unit = consignment()
    for status in (
        ConsignmentStatus.ARRIVED,
        ConsignmentStatus.INSPECTED,
        ConsignmentStatus.CATALOGUED,
        ConsignmentStatus.SOLD,
    ):
        unit = unit.with_status(status)
    assert unit.status is ConsignmentStatus.SOLD
    assert not unit.is_open


def test_a_no_sale_can_be_run_again() -> None:
    unit = consignment(status=ConsignmentStatus.NO_SALE)
    assert unit.with_status(ConsignmentStatus.CATALOGUED).status is ConsignmentStatus.CATALOGUED


def test_a_sold_unit_goes_nowhere() -> None:
    with pytest.raises(StateError):
        consignment(status=ConsignmentStatus.SOLD).with_status(ConsignmentStatus.NO_SALE)


def test_skipping_the_inspection_is_refused() -> None:
    with pytest.raises(StateError) as caught:
        consignment(status=ConsignmentStatus.ARRIVED).with_status(ConsignmentStatus.CATALOGUED)
    assert caught.value.current == "arrived"
    assert "inspected" in caught.value.wanted


def test_a_terminal_status_says_so_in_the_error() -> None:
    with pytest.raises(StateError) as caught:
        require_transition(ConsignmentStatus.RELEASED, ConsignmentStatus.CATALOGUED)
    assert caught.value.wanted == "nothing"


def test_withdrawal_is_available_from_every_live_status() -> None:
    for status, allowed in TRANSITIONS.items():
        if status.is_open:
            assert ConsignmentStatus.WITHDRAWN in allowed


def test_no_transition_leads_back_into_registered() -> None:
    for allowed in TRANSITIONS.values():
        assert ConsignmentStatus.REGISTERED not in allowed


def test_with_status_leaves_the_original_alone() -> None:
    unit = consignment()
    moved = unit.with_status(ConsignmentStatus.ARRIVED)
    assert unit.status is ConsignmentStatus.REGISTERED
    assert moved.id == unit.id


def test_the_floor_is_optional_until_it_is_set() -> None:
    assert not consignment().has_floor
    assert consignment(floor=Money.parse("4500.00")).has_floor
