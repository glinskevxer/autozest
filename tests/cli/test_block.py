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
