import sqlite3
from datetime import date

import pytest

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import IntakeService
from autozest.consignment.transport import (
    Carrier,
    Direction,
    Payer,
    TransportService,
    TransportStatus,
)
from autozest.errors import NotFound, PolicyViolation, StateError, ValidationError
from autozest.money import Money
from autozest.storage.db import table_names
from autozest.vin import Vin

ACCORD = "1HGCM82633A004352"


@pytest.fixture()
def transport(db: sqlite3.Connection) -> TransportService:
    AccountRepository(db).add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-1",
            state="TX",
        )
    )
    VehicleRepository(db).add(Vehicle(vin=Vin.parse(ACCORD), make="Honda", model="Accord"))
    return TransportService(db)


def consigned(db: sqlite3.Connection) -> str:
    return IntakeService(db).register(ACCORD, "d-100").id


def test_the_transport_tables_are_there(db: sqlite3.Connection) -> None:
    assert "carrier" in table_names(db)
    assert "transport_order" in table_names(db)


def test_carriers_come_back_sorted(transport: TransportService) -> None:
    transport.add_carrier(Carrier(id="c-2", name="Zephyr Haulage"))
    transport.add_carrier(Carrier(id="c-1", name="Apex Transport", contact="0100 900 100"))
    assert [c.name for c in transport.carriers()] == ["Apex Transport", "Zephyr Haulage"]
    assert transport.carriers()[0].contact == "0100 900 100"


def test_booking_an_inbound_movement(
    transport: TransportService, db: sqlite3.Connection
) -> None:
    unit = consigned(db)
    order = transport.book(
        unit,
        Direction.INBOUND,
        "Dallas TX",
        "the yard",
        Payer.SELLER,
        quoted=Money.parse("185.00"),
        on=date(2024, 3, 1),
    )
    assert order.id.startswith("T-2024-")
    assert order.status is TransportStatus.BOOKED
    assert order.payer is Payer.SELLER
    assert order.quoted == Money.parse("185.00")


def test_who_pays_is_recorded_not_derived(
    transport: TransportService, db: sqlite3.Connection
) -> None:
    unit = consigned(db)
    order = transport.book(unit, Direction.OUTBOUND, "the yard", "Tulsa OK", Payer.SELLER)
    assert order.direction is Direction.OUTBOUND
    assert order.payer is Payer.SELLER


def test_both_ends_are_needed(transport: TransportService, db: sqlite3.Connection) -> None:
    unit = consigned(db)
    with pytest.raises(ValidationError, match="both ends"):
        transport.book(unit, Direction.INBOUND, "  ", "the yard", Payer.SELLER)


def test_one_open_order_per_direction(
    transport: TransportService, db: sqlite3.Connection
) -> None:
    unit = consigned(db)
    transport.book(unit, Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER)
    with pytest.raises(PolicyViolation) as caught:
        transport.book(unit, Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER)
    assert caught.value.rule == "transport.already_booked"
    transport.book(unit, Direction.OUTBOUND, "the yard", "Tulsa OK", Payer.BUYER)


def test_a_cancelled_order_frees_the_direction(
    transport: TransportService, db: sqlite3.Connection
) -> None:
    unit = consigned(db)
    first = transport.book(unit, Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER)
    transport.move(first.id, TransportStatus.CANCELLED)
    assert transport.book(unit, Direction.INBOUND, "Austin TX", "the yard", Payer.SELLER)


def test_the_movement_path(transport: TransportService, db: sqlite3.Connection) -> None:
    unit = consigned(db)
    order = transport.book(unit, Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER)
    collected = transport.move(order.id, TransportStatus.COLLECTED, on=date(2024, 3, 4))
    assert collected.collected_on == date(2024, 3, 4)
    delivered = transport.move(order.id, TransportStatus.DELIVERED, on=date(2024, 3, 5))
    assert delivered.delivered_on == date(2024, 3, 5)
    assert not delivered.is_open


def test_a_delivered_order_goes_nowhere(
    transport: TransportService, db: sqlite3.Connection
) -> None:
    unit = consigned(db)
    order = transport.book(unit, Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER)
    transport.move(order.id, TransportStatus.COLLECTED)
    transport.move(order.id, TransportStatus.DELIVERED)
    with pytest.raises(StateError, match="nothing"):
        transport.move(order.id, TransportStatus.CANCELLED)


def test_a_delivery_before_a_collection_is_refused(
    transport: TransportService, db: sqlite3.Connection
) -> None:
    unit = consigned(db)
    order = transport.book(unit, Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER)
    with pytest.raises(StateError):
        transport.move(order.id, TransportStatus.DELIVERED)


def test_the_outstanding_list_empties_as_things_arrive(
    transport: TransportService, db: sqlite3.Connection
) -> None:
    unit = consigned(db)
    order = transport.book(unit, Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER)
    assert len(transport.outstanding()) == 1
    assert len(transport.outstanding(Direction.OUTBOUND)) == 0
    transport.move(order.id, TransportStatus.COLLECTED)
    assert len(transport.outstanding()) == 1
    transport.move(order.id, TransportStatus.DELIVERED)
    assert transport.outstanding() == []


def test_an_order_for_a_unit_that_is_not_consigned(transport: TransportService) -> None:
    with pytest.raises(NotFound):
        transport.book(
            "C-2024-999999", Direction.INBOUND, "Dallas TX", "the yard", Payer.SELLER
        )


def test_an_unknown_order(transport: TransportService) -> None:
    with pytest.raises(NotFound):
        transport.get("T-2024-999999")
