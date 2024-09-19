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
def site(initialised: Invoke, db_path: Path) -> Invoke:
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
    initialised("consign", "register", VIN, "d-100", "--floor", "3000.00")
    initialised("consign", "check-in", VIN, "--keys", "2", "--miles", "40000")
    return initialised


def test_adding_a_space_and_parking_in_it(site: Invoke) -> None:
    site("yard", "add-space", "s-1", "--zone", "north", "--row", "C", "--number", "14")
    parked = site("yard", "put", VIN, "s-1", "--by", "r.okafor").output
    assert "north-C014" in parked
    assert "north-C014" in site("yard", "find", VIN).output


def test_finding_a_unit_that_is_not_here_exits_four(site: Invoke) -> None:
    site("yard", "find", VIN, expect=4)


def test_moving_and_leaving(site: Invoke) -> None:
    site("yard", "add-space", "s-1", "--zone", "north", "--row", "C", "--number", "14")
    site("yard", "add-space", "s-2", "--zone", "north", "--row", "C", "--number", "15")
    site("yard", "put", VIN, "s-1", "--by", "r.okafor")
    site("yard", "put", VIN, "s-2", "--by", "r.okafor", "--reason", "under cover")
    assert "north-C015" in site("yard", "find", VIN).output
    site("yard", "out", VIN, "--by", "r.okafor", "--reason", "collected")
    site("yard", "find", VIN, expect=4)
    assert "under cover" in site("yard", "history", VIN).output


def test_free_spaces_and_occupancy(site: Invoke) -> None:
    site("yard", "add-space", "s-1", "--zone", "north", "--row", "C", "--number", "14")
    assert "1 free of 1" in site("yard", "free").output
    site("yard", "put", VIN, "s-1", "--by", "r.okafor")
    assert "0 free of 1" in site("yard", "free").output
    assert "occupied" in site("yard", "occupancy").output


def test_keys_in_and_out(site: Invoke) -> None:
    assert "tag T-0412" in site("gate", "keys-in", VIN, "T-0412", "--count", "2").output
    assert "1 sets held" in site("gate", "cabinet").output
    assert "to d-200" in site("gate", "keys-out", VIN, "d-200").output
    assert "0 sets held" in site("gate", "cabinet").output


def test_release_is_held_and_can_be_overridden(site: Invoke) -> None:
    site("gate", "release", VIN, "d-200", "--by", "r.okafor", expect=7)
    result = site(
        "gate", "release", VIN, "d-200", "--by", "m.byrne", "--override", "wire confirmed"
    )
    assert "released to d-200" in result.output
    assert "1 overrides" in site("gate", "overrides").output
