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
def desk(initialised: Invoke, db_path: Path) -> Invoke:
    conn: sqlite3.Connection = connect(db_path)
    AccountRepository(conn).add(
        Account(
            id="d-200",
            name="Kestrel Auto",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-2",
            state="TX",
        )
    )
    VehicleRepository(conn).add(
        Vehicle(vin=Vin.parse(VIN), make="Honda", model="Accord", model_year=2019)
    )
    conn.close()
    return initialised


def test_a_guide_value_and_the_advice_off_it(desk: Invoke) -> None:
    desk("market", "guide", "Honda", "Accord", "2019", "clean", "9000.00")
    output = desk("market", "advise", VIN).output
    assert "9,000.00" in output
    assert "7,920.00" in output
    assert "from" in output


def test_advice_with_nothing_to_go_on(desk: Invoke) -> None:
    output = desk("market", "advise", VIN).output
    assert "0.00" in output
    assert "nothing" in output


def test_a_cap_can_be_set_shown_and_lifted(desk: Invoke) -> None:
    result = desk(
        "limits",
        "set",
        "d-200",
        "per_unit",
        "4000.00",
        "--reason",
        "asked us to",
        "--by",
        "p.dunne",
    ).output
    reference = result.split()[0]
    assert "asked for" in result
    assert "1 live limits" in desk("limits", "show", "d-200").output
    assert "lifted" in desk("limits", "lift", reference).output
    assert "0 live limits" in desk("limits", "show", "d-200").output


def test_an_imposed_cap_says_so(desk: Invoke) -> None:
    output = desk(
        "limits",
        "set",
        "d-200",
        "per_sale",
        "20000.00",
        "--reason",
        "two late payments",
        "--by",
        "m.byrne",
        "--imposed",
    ).output
    assert "imposed" in output


def test_a_negative_cap_exits_two(desk: Invoke) -> None:
    desk(
        "limits",
        "set",
        "d-200",
        "per_unit",
        "-1.00",
        "--reason",
        "x",
        "--by",
        "p.dunne",
        expect=2,
    )
