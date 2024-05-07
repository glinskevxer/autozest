from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.storage.db import connect
from autozest.vin import Vin
from tests.cli.conftest import Invoke

VIN = "1HGCM82633A004352"


@pytest.fixture()
def clerk(initialised: Invoke, db_path: Path) -> Invoke:
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
    VehicleRepository(conn).add(Vehicle(vin=Vin.parse(VIN), make="Honda", model="Accord"))
    conn.close()
    return initialised


def consigned(run_cli: Invoke, *, declare: str | None = None) -> None:
    run_cli("sale", "create", "2024-03-12", "--title", "Tuesday")
    run_cli("consign", "register", VIN, "d-100", "--floor", "3000.00")
    run_cli("consign", "check-in", VIN, "--keys", "2", "--miles", "40000")
    report = run_cli("inspect", "open", VIN, "--inspector", "j.hale").output.split()[1]
    args = ["inspect", "sign", report, "--value", "12000.00"]
    if declare:
        args += ["--declare", declare]
    run_cli(*args)
    run_cli("sale", "catalogue", "S-20240312", "--lane", "A")


def sell(run_cli: Invoke) -> None:
    run_cli("sale", "open", "S-20240312")
    run_cli("block", "bid", "S-20240312", "A001", "d-200", "8200.00")
    run_cli("block", "fall", "S-20240312", "A001")


def test_expecting_and_receiving(clerk: Invoke) -> None:
    consigned(clerk)
    assert "title is expected" in clerk("title", "expect", VIN).output
    received = clerk("title", "receive", VIN, "--state", "TX", "--number", "TX99182").output
    assert "title on hand" in received
    assert "brand clean" in received


def test_an_undisclosed_brand_exits_seven_and_says_so(clerk: Invoke) -> None:
    consigned(clerk)
    sell(clerk)
    clerk("title", "expect", VIN)
    result = clerk("title", "receive", VIN, "--brand", "salvage", expect=7)
    assert "undisclosed salvage brand" in result.output
    assert "regardless of any window" in result.output


def test_a_declared_brand_is_no_surprise(clerk: Invoke) -> None:
    consigned(clerk, declare="salvage_title")
    sell(clerk)
    clerk("title", "expect", VIN)
    assert "brand salvage" in clerk("title", "receive", VIN, "--brand", "salvage").output


def test_holds_list_what_is_in_the_way(clerk: Invoke) -> None:
    consigned(clerk)
    clerk("title", "expect", VIN)
    output = clerk("title", "holds", VIN).output
    assert "the title is expected" in output
    assert "the unit has not sold" in output
    assert "2 holds" in output


def test_sending_a_held_title_exits_seven(clerk: Invoke) -> None:
    consigned(clerk)
    clerk("title", "expect", VIN)
    clerk("title", "send", VIN, expect=7)


def test_the_whole_paperwork_path(clerk: Invoke) -> None:
    consigned(clerk)
    sell(clerk)
    clerk("title", "expect", VIN)
    clerk("title", "receive", VIN, "--state", "TX", "--number", "TX99182")
    assert "0 holds" in clerk("title", "holds", VIN).output
    assert "sent to the buyer" in clerk("title", "send", VIN).output


def test_marking_a_title_into_the_attorney_lane(clerk: Invoke) -> None:
    consigned(clerk)
    clerk("title", "expect", VIN)
    assert (
        "is attorney"
        in clerk("title", "mark", VIN, "attorney", "--detail", "chasing a duplicate").output
    )


def test_releasing_a_no_sale_title_back(clerk: Invoke) -> None:
    consigned(clerk)
    clerk("title", "expect", VIN)
    clerk("title", "receive", VIN)
    assert "back to the seller" in clerk("title", "release", VIN).output


def test_show_and_outstanding(clerk: Invoke) -> None:
    consigned(clerk)
    clerk("title", "expect", VIN)
    assert "1 outstanding" in clerk("title", "outstanding").output
    shown = clerk("title", "show", VIN).output
    assert "movements" in shown
    assert "new to expected" in shown
    clerk("title", "receive", VIN)
    assert "0 outstanding" in clerk("title", "outstanding").output
