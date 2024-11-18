"""The one place a typed token turns into a record."""

from __future__ import annotations

from pathlib import Path

import pytest

from autozest.block.models import Run
from autozest.cli import lookup
from autozest.cli.context import Context
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import Consignment
from autozest.errors import NotFound
from autozest.inspection.announcements import Light
from autozest.storage.db import connect
from autozest.storage.migrator import migrate

VIN = "1HGCM82633A004352"


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    path = tmp_path / "sale.sqlite3"
    conn = connect(path)
    migrate(conn)
    from datetime import date

    from autozest.block.sales import SaleRepository
    from autozest.catalog.accounts import Account, AccountClass, AccountRepository
    from autozest.catalog.vehicles import Vehicle, VehicleRepository
    from autozest.vin import Vin

    AccountRepository(conn).add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-1",
            state="TX",
        )
    )
    VehicleRepository(conn).add(Vehicle(vin=Vin.parse(VIN), make="Honda", model="Accord"))
    intake = IntakeService(conn)
    unit = intake.register(VIN, "d-100")
    intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
    sales = SaleRepository(conn)
    sale = sales.create(date(2024, 3, 12), "Tuesday", {"A": 10})
    from autozest.consignment.models import ConsignmentStatus

    for status in (ConsignmentStatus.INSPECTED, ConsignmentStatus.CATALOGUED):
        intake.consignments.save_status(intake.consignments.get(unit.id), status)
    sales.add_run(sale.code, "A", unit.id, Light.GREEN)
    conn.close()
    return Context.build(None, str(path))


def test_a_reference_finds_the_consignment(ctx: Context) -> None:
    found = lookup.consignment(ctx, "C-2026-000001")
    assert isinstance(found, Consignment)
    assert found.vin == VIN


def test_a_vin_finds_it_too(ctx: Context) -> None:
    assert lookup.consignment(ctx, VIN.lower()).vin == VIN


def test_a_gate_pass_finds_it_as_well(ctx: Context) -> None:
    unit = lookup.consignment(ctx, VIN)
    assert unit.gate_pass is not None
    assert lookup.consignment_id(ctx, unit.gate_pass) == unit.id


def test_something_that_is_nothing(ctx: Context) -> None:
    with pytest.raises(NotFound):
        lookup.consignment(ctx, "ZZZZZ")


def test_a_run_number_finds_the_run(ctx: Context) -> None:
    found = lookup.run(ctx, "S-20240312", "A001")
    assert isinstance(found, Run)
    assert found.number == "A001"
    assert lookup.run_id(ctx, "S-20240312", "A001") == found.id


def test_a_run_number_that_is_not_there(ctx: Context) -> None:
    with pytest.raises(NotFound):
        lookup.run(ctx, "S-20240312", "A999")
