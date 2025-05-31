"""The whole building, driven the way a week actually runs it.

Every other command line test checks one group. This drives all of them in
order, through the installed entry point's own code path, and it is the test
that would catch a command that works on its own and not after the one before
it.

It is long on purpose. Splitting it would lose the only thing it is for, which
is that the order matters.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.storage.db import connect
from autozest.vin import Vin
from tests.cli.conftest import Invoke

VINS = ["1HGCM82633A004352", "5YJ3E1EA0KF000316", "JTDKN3DU8A0000001"]


@pytest.fixture()
def building(initialised: Invoke, db_path: Path) -> Invoke:
    conn: sqlite3.Connection = connect(db_path)
    accounts = AccountRepository(conn)
    for account_id, name in (
        ("d-100", "Ridgeline Motors"),
        ("d-200", "Kestrel Auto"),
        ("d-300", "Cotton Lane Motors"),
    ):
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


def test_a_week_in_the_building(building: Invoke) -> None:
    az = building

    # Monday. The template builds Tuesday's sale and the yard is set up.
    az(
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
        "A:100",
    )
    az("sale", "template", "build", "--id", "t-1", "--on", "2024-03-19")
    az("yard", "add-space", "s-1", "--zone", "north", "--row", "C", "--number", "14")
    az("roster", "add", "s-a", "Della Voss", "auctioneer")
    az("roster", "add", "s-r", "Ray Okafor", "ring")
    az("roster", "assign", "S-20240319", "A", "s-a")
    az("roster", "assign", "S-20240319", "A", "s-r")
    assert "0 lanes short" in az("roster", "gaps", "S-20240319").output

    # A unit arrives, is parked, inspected and catalogued.
    az("consign", "register", VINS[0], "d-100", "--floor", "5000.00")
    az("consign", "check-in", VINS[0], "--keys", "2", "--miles", "41930")
    az("yard", "put", VINS[0], "s-1", "--by", "r.okafor")
    az("gate", "keys-in", VINS[0], "T-0412", "--count", "2")
    report = az("inspect", "open", VINS[0], "--inspector", "j.hale").output.split()[1]
    az(
        "inspect",
        "damage",
        report,
        "--panel",
        "hood",
        "--kind",
        "dent",
        "--severity",
        "3",
        "--estimate",
        "400.00",
    )
    az("inspect", "sign", report, "--value", "30000.00")
    az("sale", "catalogue", "S-20240319", "--lane", "A")
    assert "ready" in az("sale", "ready", "S-20240319").output

    # Tuesday. Doors, then the block.
    az("block", "register", "S-20240319", "d-200")
    az("sale", "open", "S-20240319")
    az("block", "bid", "S-20240319", "A001", "d-200", "8200.00")
    assert "sold" in az("block", "fall", "S-20240319", "A001").output
    assert "1 sold of 1" in az("sale", "close", "S-20240319").output

    # Wednesday. Post, take the money, chase the title.
    az("settle", "post", "S-20240319")
    az("settle", "pay", "d-200", "8545.00", "--reference", "WIRE-1")
    az("title", "expect", VINS[0])
    az("title", "receive", VINS[0], "--state", "TX", "--number", "TX99182")
    assert "0 holds" in az("settle", "holds", VINS[0]).output
    assert "released 7,965.00" in az("settle", "release", VINS[0]).output
    az("title", "send", VINS[0])
    az("gate", "keys-out", VINS[0], "d-200")
    az("gate", "release", VINS[0], "d-200", "--by", "r.okafor")
    az("yard", "out", VINS[0], "--by", "r.okafor", "--reason", "collected")

    # Thursday. The books, and everything that reads them.
    assert "balanced" in az("settle", "trial-balance").output
    assert "0 problems" in az("admin", "check").output
    assert "nothing wrong" in az("admin", "doctor").output
    assert "healthy" in az("admin", "health").output
    for command in (
        ("report", "recap", "S-20240319"),
        ("report", "dashboard", "--sale", "S-20240319"),
        ("report", "sellers"),
        ("report", "buyers"),
        ("report", "aging"),
        ("report", "turnaround"),
        ("report", "concentration", "S-20240319"),
        ("report", "reference"),
        ("admin", "manual"),
        ("admin", "history"),
    ):
        assert az(*command).output


def test_the_audit_trail_has_the_week_on_it(building: Invoke) -> None:
    az = building
    az("consign", "register", VINS[0], "d-100", "--floor", "5000.00")
    az("consign", "check-in", VINS[0], "--keys", "2", "--miles", "41930")
    az("consign", "withdraw", VINS[0], "sold retail")
    trail = az("admin", "audit").output
    assert "consign.register" in trail
    assert "consign.check-in" in trail
    assert "consign.withdraw" in trail
    assert "3 events of 3" in trail


def test_a_disclosure_miss_is_caught_at_the_end_of_the_week(building: Invoke) -> None:
    az = building
    az("sale", "create", "2024-03-19", "--title", "Tuesday dealer sale")
    az("consign", "register", VINS[0], "d-100", "--floor", "5000.00")
    az("consign", "check-in", VINS[0], "--keys", "2", "--miles", "41930")
    report = az("inspect", "open", VINS[0], "--inspector", "j.hale").output.split()[1]
    az("inspect", "sign", report, "--value", "30000.00")
    az("sale", "catalogue", "S-20240319", "--lane", "A")
    az("sale", "open", "S-20240319")
    az("block", "bid", "S-20240319", "A001", "d-200", "8200.00")
    az("block", "fall", "S-20240319", "A001")
    az("title", "expect", VINS[0])
    az("title", "receive", VINS[0], "--brand", "salvage", expect=7)
    result = az("report", "disclosure", "S-20240319", expect=1)
    assert "missed" in result.output
    assert "needs looking at" in result.output
