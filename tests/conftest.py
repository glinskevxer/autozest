"""Fixtures every layer of the suite shares.

The sale floor fixture lives here rather than beside the block tests because
the reporting and settlement suites want the same set up, and copying it into
each package is how three slightly different sale floors end up in one suite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date

import pytest

from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import ConsignmentStatus
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.storage.db import connect_memory
from autozest.storage.migrator import migrate
from autozest.vin import Vin


@pytest.fixture(scope="session")
def schema_template() -> Iterator[sqlite3.Connection]:
    """The migrated schema, built once for the whole run.

    Running every migration for every test was costing about a third of the
    suite. The scripts are identical each time and sqlite can copy a database
    far faster than it can execute the statements that built it.
    """
    conn = connect_memory()
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture()
def db(schema_template: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A migrated in memory database, thrown away after each test."""
    conn = connect_memory()
    schema_template.backup(conn)
    yield conn
    conn.close()


SALE_DAY = date(2024, 3, 12)
VINS = [
    "1HGCM82633A004352",
    "5YJ3E1EA0KF000316",
    "JTDKN3DU8A0000001",
    "WBA3A5C52DF000001",
    "KMHD35LEXEU000001",
]


@dataclass
class Floor:
    """A sale that is set up and ready to have units put through it."""

    conn: sqlite3.Connection
    sales: SaleRepository
    intake: IntakeService
    code: str
    catalogue: Callable[..., int]


@pytest.fixture()
def floor(db: sqlite3.Connection) -> Floor:
    accounts = AccountRepository(db)
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
    accounts.add(
        Account(
            id="f-1",
            name="Ninth Street Finance",
            account_class=AccountClass.FINANCE,
            dealer_licence="TX-f1",
            state="TX",
        )
    )
    vehicles = VehicleRepository(db)
    for index, vin in enumerate(VINS):
        vehicles.add(Vehicle(vin=Vin.parse(vin), make="Make", model=f"Model{index}"))

    sales = SaleRepository(db)
    intake = IntakeService(db)
    sale = sales.create(SALE_DAY, "Tuesday dealer sale", {"A": 100, "B": 100})

    used: list[str] = []

    def catalogue(
        *,
        floor_price: str | None = "5000.00",
        seller: str = "d-100",
        light: Light = Light.GREEN,
    ) -> int:
        vin = VINS[len(used)]
        used.append(vin)
        unit = intake.register(
            vin, seller, floor=Money.parse(floor_price) if floor_price else None
        )
        intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
        for status in (ConsignmentStatus.INSPECTED, ConsignmentStatus.CATALOGUED):
            intake.consignments.save_status(intake.consignments.get(unit.id), status)
        run = sales.add_run(sale.code, "A", unit.id, light)
        assert run.id is not None
        return run.id

    return Floor(conn=db, sales=sales, intake=intake, code=sale.code, catalogue=catalogue)
