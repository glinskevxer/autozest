import sqlite3

import pytest

from autozest.storage.db import column_names, connect_memory, integrity_problems, table_names
from autozest.storage.migrator import current_version, load_migrations, migrate
from autozest.tools.check_migrations import main, problems


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = connect_memory()
    migrate(conn)
    return conn


def test_the_shipped_migrations_apply_to_an_empty_database(db: sqlite3.Connection) -> None:
    assert current_version(db) == len(load_migrations())
    assert integrity_problems(db) == []


def test_the_core_tables_are_there(db: sqlite3.Connection) -> None:
    for table in ("account", "vehicle", "odometer_reading"):
        assert table in table_names(db)


def test_an_account_carries_its_class_and_status(db: sqlite3.Connection) -> None:
    assert column_names(db, "account") == [
        "id",
        "name",
        "class",
        "status",
        "tax_id",
        "dealer_licence",
        "state",
        "created_at",
    ]


def test_an_account_class_outside_the_list_is_refused(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO account (id, name, class, created_at) VALUES (?, ?, ?, ?)",
            ("a1", "Ridgeline Motors", "hobbyist", "2024-01-02T09:00:00+00:00"),
        )


def test_the_same_name_may_trade_in_two_states(db: sqlite3.Connection) -> None:
    for state in ("TX", "OK"):
        db.execute(
            "INSERT INTO account (id, name, class, state, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                f"a-{state}",
                "Ridgeline Motors",
                "independent_dealer",
                state,
                "2024-01-02T09:00:00+00:00",
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO account (id, name, class, state, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "a-dupe",
                "Ridgeline Motors",
                "independent_dealer",
                "TX",
                "2024-01-02T09:00:00+00:00",
            ),
        )


def test_a_vehicle_year_outside_the_plausible_range_is_refused(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO vehicle (vin, model_year, make, model, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("1HGCM82633A004352", 1500, "Honda", "Accord", "2024-01-02T09:00:00+00:00"),
        )


def test_readings_hang_off_a_vehicle_and_go_with_it(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO vehicle (vin, model_year, make, model, created_at) VALUES (?, ?, ?, ?, ?)",
        ("1HGCM82633A004352", 2003, "Honda", "Accord", "2024-01-02T09:00:00+00:00"),
    )
    for miles in (180_000, 181_200):
        db.execute(
            "INSERT INTO odometer_reading (vin, miles, read_on, source, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("1HGCM82633A004352", miles, "2024-01-02", "intake", "2024-01-02T09:00:00+00:00"),
        )
    assert db.execute("SELECT COUNT(*) AS n FROM odometer_reading").fetchone()["n"] == 2
    db.execute("DELETE FROM vehicle WHERE vin = ?", ("1HGCM82633A004352",))
    assert db.execute("SELECT COUNT(*) AS n FROM odometer_reading").fetchone()["n"] == 0


def test_a_reading_for_an_unknown_vin_is_refused(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO odometer_reading (vin, miles, read_on, source, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("1HGCM82633A004352", 100, "2024-01-02", "intake", "2024-01-02T09:00:00+00:00"),
        )


def test_the_migration_checker_is_happy_with_what_ships() -> None:
    assert problems() == []


def test_the_checker_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert "no problems" in capsys.readouterr().out
