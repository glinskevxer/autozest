from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.storage.db import connect
from autozest.vin import Vin
from tests.cli.conftest import Invoke

VINS = [
    "1HGCM82633A004352",
    "5YJ3E1EA0KF000316",
    "JTDKN3DU8A0000001",
]


@pytest.fixture()
def ring(initialised: Invoke, db_path: Path) -> Invoke:
    conn: sqlite3.Connection = connect(db_path)
    accounts = AccountRepository(conn)
    for account_id, name in (("d-100", "Ridgeline Motors"), ("d-200", "Kestrel Auto")):
        accounts.add(
            Account(
                id=account_id,
                name=name,
                account_class=AccountClass.INDEPENDENT_DEALER,
                dealer_licence=f"TX-{account_id}",
                state="TX",
            )
        )
    vehicles = VehicleRepository(conn)
    for index, vin in enumerate(VINS):
        vehicles.add(Vehicle(vin=Vin.parse(vin), make="Make", model=f"Model{index}"))
    conn.close()
    return initialised


def prepared(run_cli: Invoke, vin: str, floor: str = "5000.00") -> None:
    run_cli("consign", "register", vin, "d-100", "--floor", floor)
    run_cli("consign", "check-in", vin, "--keys", "2", "--miles", "40000")
    report = run_cli("inspect", "open", vin, "--inspector", "j.hale").output.split()[1]
    run_cli("inspect", "sign", report, "--value", "12000.00")


def test_creating_a_sale_with_two_lanes(ring: Invoke) -> None:
    output = ring(
        "sale", "create", "2024-03-12", "--title", "Tuesday", "--lanes", "A:50,B:50"
    ).output
    assert "S-20240312" in output
    assert "A, B" in output


def test_the_full_night(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    catalogued = ring("sale", "catalogue", "S-20240312", "--lane", "A").output
    assert "A001" in catalogued
    assert "green" in catalogued
    ring("sale", "open", "S-20240312")
    ring("block", "bid", "S-20240312", "A001", "d-200", "5200.00")
    fell = ring("block", "fall", "S-20240312", "A001").output
    assert "sold" in fell
    assert "5,200.00" in fell
    closed = ring("sale", "close", "S-20240312").output
    assert "1 sold of 1" in closed


def test_a_short_bid_goes_on_if_and_can_be_countered(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("sale", "open", "S-20240312")
    ring("block", "bid", "S-20240312", "A001", "d-200", "4200.00")
    assert "on if" in ring("block", "fall", "S-20240312", "A001").output
    listed = ring("block", "if-list", "S-20240312").output
    assert "1 on if" in listed
    assert "800.00" in listed
    ring("block", "counter", "S-20240312", "A001", "4600.00")
    assert "4,600.00" in ring("block", "accept", "S-20240312", "A001").output


def test_declining_an_if_bid(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("sale", "open", "S-20240312")
    ring("block", "bid", "S-20240312", "A001", "d-200", "4200.00")
    ring("block", "fall", "S-20240312", "A001")
    assert "no sale" in ring("block", "decline", "S-20240312", "A001").output


def test_the_run_sheet(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    for vin in VINS[:2]:
        prepared(ring, vin)
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    sheet = ring("sale", "runs", "S-20240312").output
    assert "2 runs" in sheet
    assert VINS[0] in sheet
    assert "Tuesday" in sheet


def test_showing_one_run_with_its_bids(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("sale", "open", "S-20240312")
    ring("block", "bid", "S-20240312", "A001", "d-200", "4200.00")
    output = ring("block", "show", "S-20240312", "A001").output
    assert "bids" in output
    assert "4,200.00" in output


def test_bidding_on_a_sale_that_is_not_open_exits_seven(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("block", "bid", "S-20240312", "A001", "d-200", "4200.00", expect=7)


def test_an_unknown_run_exits_four(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("sale", "open", "S-20240312")
    ring("block", "bid", "S-20240312", "A999", "d-200", "4200.00", expect=4)


def test_relighting_before_the_sale_opens(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    moved = ring("sale", "relight", "S-20240312", "A001", "yellow", "seller asked").output
    assert "now yellow" in moved
    ring("sale", "open", "S-20240312")
    ring("sale", "relight", "S-20240312", "A001", "red", "too late", expect=7)


def test_a_cheap_unit_catalogues_red(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0], floor="900.00")
    output = ring("sale", "catalogue", "S-20240312", "--lane", "A").output
    assert "red" in output
    assert "floor under" in output


def test_listing_sales(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-05", "--title", "First")
    ring("sale", "create", "2024-03-12", "--title", "Second")
    output = ring("sale", "list").output
    assert output.index("Second") < output.index("First")


def test_the_recap_prints(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("sale", "open", "S-20240312")
    ring("block", "bid", "S-20240312", "A001", "d-200", "5200.00")
    ring("block", "fall", "S-20240312", "A001")
    output = ring("report", "recap", "S-20240312").output
    assert "sale recap" in output
    assert "5,200.00" in output
    assert "sell through" in output


def test_the_door_list(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    assert "badge 1 for d-200" in ring("block", "register", "S-20240312", "d-200").output
    assert "1 registered" in ring("block", "door", "S-20240312").output


def test_a_requested_badge_is_honoured(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    output = ring("block", "register", "S-20240312", "d-200", "--badge", "40").output
    assert "badge 40" in output


def test_a_clean_buyer_has_no_blockers(ring: Invoke) -> None:
    assert "0 blockers" in ring("block", "blockers", "d-200").output


def test_bidding_without_a_badge_once_the_list_exists(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("block", "register", "S-20240312", "d-100")
    ring("sale", "open", "S-20240312")
    ring("block", "bid", "S-20240312", "A001", "d-200", "5200.00", expect=7)


def test_the_reference_card_prints(ring: Invoke) -> None:
    output = ring("report", "reference").output
    assert "The lights" in output
    assert "48 business hours" in output
    assert "card 2024.1" in output


def test_one_section_of_the_card(ring: Invoke) -> None:
    output = ring("report", "reference", "fees").output
    assert "card 2024.1" in output
    assert "The lights" not in output


def test_readiness_exits_non_zero_until_the_sale_can_open(ring: Invoke) -> None:
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    result = ring("sale", "ready", "S-20240312", expect=1)
    assert "nothing is catalogued" in result.output
    assert "must be done first" in result.output


def test_the_two_groups_are_separate_modules() -> None:
    """A regression guard on the split, not a style opinion.

    The two groups have different users on different days and one file holding
    both had grown past the point where either could be read. Importing them
    from the wrong place is how they grow back together.
    """
    from autozest.cli import block as block_module
    from autozest.cli import sale as sale_module

    assert hasattr(block_module, "block")
    assert hasattr(sale_module, "sale")
    assert not hasattr(block_module, "sale")
    assert not hasattr(sale_module, "block")


def test_every_block_command_is_live(ring: Invoke) -> None:
    from autozest.cli.block import block as block_group

    names = set(block_group.commands)
    assert {"bid", "fall", "counter", "accept", "decline", "if-list", "show"} <= names
    assert {"register", "door", "blockers"} <= names


def test_every_sale_command_is_live(ring: Invoke) -> None:
    from autozest.cli.sale import sale as sale_group

    names = set(sale_group.commands)
    assert {"create", "catalogue", "runs", "open", "close", "list"} <= names
    assert {"relight", "ready"} <= names


def test_the_rule_catalogue_prints(ring: Invoke) -> None:
    output = ring("report", "reference", "rules").output
    assert "arb.announced" in output
    assert "bid.own_unit" in output
    assert "rules" in output


def test_the_exit_statuses_print(ring: Invoke) -> None:
    output = ring("report", "reference", "statuses").output
    assert "a rule forbids it" in output
    assert "the record is in the wrong state" in output


def test_the_documented_statuses_are_the_ones_the_command_line_uses(
    ring: Invoke,
) -> None:
    """The card is only worth printing if it is true.

    Each of these is a real command that fails in a real way, checked against
    the status the card claims it will give.
    """
    ring("sale", "create", "2024-03-12", "--title", "Tuesday")
    ring("sale", "create", "nonsense", "--title", "Tuesday", expect=2)
    ring("sale", "runs", "S-19990101", expect=4)
    ring("sale", "create", "2024-03-12", "--title", "Again", expect=5)
    prepared(ring, VINS[0])
    ring("sale", "catalogue", "S-20240312", "--lane", "A")
    ring("block", "bid", "S-20240312", "A001", "d-200", "5200.00", expect=7)


def test_a_template_is_added_listed_and_built(ring: Invoke) -> None:
    added = ring(
        "sale",
        "template",
        "add",
        "--id",
        "t-1",
        "--name",
        "Tuesday dealer",
        "--weekday",
        "1",
        "--title",
        "Tuesday dealer sale",
        "--lanes",
        "A:120,B:120",
    ).output
    assert "every Tuesday" in added
    assert "240 slots" in added
    assert "t-1" in ring("sale", "template", "list").output
    built = ring("sale", "template", "build", "--id", "t-1", "--on", "2024-03-19").output
    assert "created S-20240319" in built


def test_building_a_template_on_the_wrong_day_exits_seven(ring: Invoke) -> None:
    ring(
        "sale",
        "template",
        "add",
        "--id",
        "t-1",
        "--name",
        "Tuesday dealer",
        "--weekday",
        "1",
        "--title",
        "Tuesday dealer sale",
    )
    ring("sale", "template", "build", "--id", "t-1", "--on", "2024-03-21", expect=7)


def test_add_without_its_options_is_a_usage_error(ring: Invoke) -> None:
    ring("sale", "template", "add", "--id", "t-1", expect=2)


def test_retiring_a_template(ring: Invoke) -> None:
    ring(
        "sale",
        "template",
        "add",
        "--id",
        "t-1",
        "--name",
        "Tuesday dealer",
        "--weekday",
        "1",
        "--title",
        "Tuesday dealer sale",
    )
    assert "retired" in ring("sale", "template", "retire", "--id", "t-1").output
    assert "t-1" not in ring("sale", "template", "list").output
