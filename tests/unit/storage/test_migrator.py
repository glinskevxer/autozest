import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from autozest.errors import ConfigError
from autozest.storage.db import connect_memory, table_names
from autozest.storage.migrator import (
    Migration,
    applied,
    current_version,
    load_migrations,
    migrate,
    pending,
    verify,
)


@pytest.fixture()
def conn() -> Iterator[sqlite3.Connection]:
    conn = connect_memory()
    yield conn
    conn.close()


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    (tmp_path / "001_lanes.sql").write_text(
        "CREATE TABLE lane (code TEXT PRIMARY KEY, capacity INTEGER NOT NULL);"
    )
    (tmp_path / "002_runs.sql").write_text(
        "CREATE TABLE run (id INTEGER PRIMARY KEY, lane TEXT NOT NULL REFERENCES lane(code));"
    )
    return tmp_path


def test_a_migration_names_itself_from_its_number_and_name() -> None:
    assert Migration(7, "arbitration_claim", "").filename == "007_arbitration_claim.sql"


def test_the_checksum_follows_the_text() -> None:
    assert Migration(1, "a", "SELECT 1").checksum == Migration(2, "b", "SELECT 1").checksum
    assert Migration(1, "a", "SELECT 1").checksum != Migration(1, "a", "SELECT 2").checksum


def test_loading_reads_them_in_number_order(folder: Path) -> None:
    loaded = load_migrations(folder)
    assert [m.number for m in loaded] == [1, 2]
    assert [m.name for m in loaded] == ["lanes", "runs"]


def test_files_that_are_not_sql_are_ignored(folder: Path) -> None:
    (folder / "README.md").write_text("notes about the schema")
    assert len(load_migrations(folder)) == 2


def test_a_badly_named_sql_file_stops_everything(folder: Path) -> None:
    (folder / "fixup.sql").write_text("SELECT 1;")
    with pytest.raises(ConfigError, match="not named like a migration"):
        load_migrations(folder)


def test_two_migrations_with_the_same_number_are_refused(folder: Path) -> None:
    (folder / "002_runs_again.sql").write_text("SELECT 1;")
    with pytest.raises(ConfigError, match="numbered 002"):
        load_migrations(folder)


def test_a_gap_in_the_numbering_is_refused(folder: Path) -> None:
    (folder / "004_titles.sql").write_text("SELECT 1;")
    with pytest.raises(ConfigError, match="skip a number"):
        load_migrations(folder)


def test_a_missing_directory_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no migrations directory"):
        load_migrations(tmp_path / "nowhere")


def test_an_untouched_database_is_at_version_zero(conn: sqlite3.Connection) -> None:
    assert current_version(conn) == 0
    assert applied(conn) == {}


def test_migrating_applies_everything_and_records_it(
    conn: sqlite3.Connection, folder: Path
) -> None:
    ran = migrate(conn, folder)
    assert [m.number for m in ran] == [1, 2]
    assert current_version(conn) == 2
    assert "lane" in table_names(conn)
    assert "run" in table_names(conn)


def test_migrating_twice_does_nothing_the_second_time(
    conn: sqlite3.Connection, folder: Path
) -> None:
    migrate(conn, folder)
    assert migrate(conn, folder) == []
    assert pending(conn, folder) == []


def test_only_the_outstanding_ones_run(conn: sqlite3.Connection, folder: Path) -> None:
    migrate(conn, folder)
    (folder / "003_titles.sql").write_text("CREATE TABLE title (id INTEGER PRIMARY KEY);")
    ran = migrate(conn, folder)
    assert [m.number for m in ran] == [3]


def test_an_edited_migration_stops_the_next_upgrade(
    conn: sqlite3.Connection, folder: Path
) -> None:
    migrate(conn, folder)
    (folder / "001_lanes.sql").write_text("CREATE TABLE lane (code TEXT PRIMARY KEY);")
    with pytest.raises(ConfigError, match="has changed since it was applied"):
        pending(conn, folder)


def test_an_applied_migration_whose_file_vanished_stops_the_upgrade(
    conn: sqlite3.Connection, folder: Path
) -> None:
    migrate(conn, folder)
    (folder / "002_runs.sql").unlink()
    with pytest.raises(ConfigError, match="its file is not on disk"):
        pending(conn, folder)


def test_verify_is_quiet_when_the_files_match(folder: Path) -> None:
    loaded = load_migrations(folder)
    done = {
        m.number: {"name": m.name, "checksum": m.checksum, "applied_at": "x"} for m in loaded
    }
    verify(loaded, done)


def test_a_failing_migration_leaves_the_earlier_ones_applied(
    conn: sqlite3.Connection, folder: Path
) -> None:
    (folder / "003_broken.sql").write_text("CREATE TABLE oops (;")
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, folder)
    assert current_version(conn) == 2
    assert "lane" in table_names(conn)


def test_the_recorded_row_carries_a_timestamp(conn: sqlite3.Connection, folder: Path) -> None:
    migrate(conn, folder)
    row = applied(conn)[1]
    assert row["name"] == "lanes"
    assert row["applied_at"].startswith("20")
    assert row["applied_at"].endswith("+00:00")
