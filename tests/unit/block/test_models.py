import sqlite3
from datetime import date

import pytest

from autozest.block.models import (
    RUN_TRANSITIONS,
    SALE_TRANSITIONS,
    Lane,
    Run,
    RunStatus,
    Sale,
    SaleStatus,
)
from autozest.errors import StateError, ValidationError
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.storage.db import table_names


def sale(**kwargs: object) -> Sale:
    defaults: dict[str, object] = {
        "code": "S-20240312",
        "sale_date": date(2024, 3, 12),
        "title": "Tuesday dealer sale",
    }
    defaults.update(kwargs)
    return Sale(**defaults)  # type: ignore[arg-type]


def run(**kwargs: object) -> Run:
    defaults: dict[str, object] = {
        "id": None,
        "sale_code": "S-20240312",
        "lane": "C",
        "position": 214,
        "consignment_id": "C-2024-000412",
        "light": Light.GREEN,
    }
    defaults.update(kwargs)
    return Run(**defaults)  # type: ignore[arg-type]


def test_the_block_tables_are_there(db: sqlite3.Connection) -> None:
    for table in ("sale", "lane", "run", "bid"):
        assert table in table_names(db)


def test_a_sale_needs_a_title() -> None:
    with pytest.raises(ValidationError, match="needs a title"):
        sale(title="  ")


def test_a_sale_runs_planned_open_closed_settled() -> None:
    current = sale()
    for status in (SaleStatus.OPEN, SaleStatus.CLOSED, SaleStatus.SETTLED):
        current = current.with_status(status)
    assert current.status is SaleStatus.SETTLED


def test_a_sale_cannot_skip_from_planned_to_closed() -> None:
    with pytest.raises(StateError):
        sale().with_status(SaleStatus.CLOSED)


def test_a_settled_sale_goes_nowhere() -> None:
    assert SALE_TRANSITIONS[SaleStatus.SETTLED] == frozenset()
    with pytest.raises(StateError, match="nothing"):
        sale(status=SaleStatus.SETTLED).with_status(SaleStatus.OPEN)


def test_only_a_planned_sale_takes_catalogue_changes() -> None:
    assert SaleStatus.PLANNED.accepts_catalogue_changes
    assert not SaleStatus.OPEN.accepts_catalogue_changes
    assert SaleStatus.CLOSED.is_finished
    assert not SaleStatus.OPEN.is_finished


def test_a_lane_letter_is_one_letter() -> None:
    Lane(sale_code="S-20240312", letter="C", capacity=120)
    for letter in ("CC", "1", ""):
        with pytest.raises(ValidationError, match="lane letter"):
            Lane(sale_code="S-20240312", letter=letter, capacity=120)


def test_a_lane_holds_at_least_one_unit() -> None:
    with pytest.raises(ValidationError, match="cannot hold"):
        Lane(sale_code="S-20240312", letter="C", capacity=0)


def test_a_run_names_itself_the_way_the_block_screen_does() -> None:
    assert run().number == "C214"
    assert run(lane="a", position=7).number == "A007"


def test_a_run_position_starts_at_one() -> None:
    with pytest.raises(ValidationError, match="not a run position"):
        run(position=0)


def test_a_sold_run_needs_a_price_and_a_buyer() -> None:
    with pytest.raises(ValidationError, match="needs a hammer price"):
        run(status=RunStatus.SOLD, buyer_id="d-200")
    with pytest.raises(ValidationError, match="needs a buyer"):
        run(status=RunStatus.SOLD, hammer=Money.parse("12350.00"))
    run(status=RunStatus.SOLD, hammer=Money.parse("12350.00"), buyer_id="d-200")


def test_red_leaves_no_arbitration_and_the_others_do() -> None:
    assert not run(light=Light.RED).arbitration_is_possible
    for light in (Light.GREEN, Light.YELLOW, Light.BLUE):
        assert run(light=light).arbitration_is_possible


def test_an_if_bid_can_still_land_either_way() -> None:
    assert RUN_TRANSITIONS[RunStatus.IF_BID] == frozenset({RunStatus.SOLD, RunStatus.NO_SALE})
    pending = run(status=RunStatus.IF_BID)
    assert pending.with_status(RunStatus.NO_SALE).status is RunStatus.NO_SALE


def test_a_scheduled_run_can_go_four_ways() -> None:
    assert len(RUN_TRANSITIONS[RunStatus.SCHEDULED]) == 4


def test_a_settled_run_does_not_move_again() -> None:
    for status in (RunStatus.SOLD, RunStatus.NO_SALE, RunStatus.PULLED):
        assert status.is_settled
        assert RUN_TRANSITIONS[status] == frozenset()
    assert not RunStatus.IF_BID.is_settled
    assert not RunStatus.SCHEDULED.is_settled


def test_an_if_bid_cannot_be_pulled_after_the_fact() -> None:
    with pytest.raises(StateError):
        run(status=RunStatus.IF_BID).with_status(RunStatus.PULLED)
