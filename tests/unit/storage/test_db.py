import sqlite3
from pathlib import Path

import pytest

from autozest.errors import ConfigError, Conflict
from autozest.storage.db import (
    column_names,
    connect,
    connect_memory,
    foreign_keys_are_on,
    integrity_problems,
    table_names,
    transaction,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    connection = connect_memory()
    connection.execute("CREATE TABLE lane (code TEXT PRIMARY KEY, capacity INTEGER)")
    connection.execute(
        "CREATE TABLE run (id INTEGER PRIMARY KEY, lane TEXT NOT NULL "
        "REFERENCES lane(code), position INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO lane VALUES ('A', 120)")
    return connection


def test_rows_come_back_as_dicts_keyed_by_column(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT code, capacity FROM lane").fetchone()
    assert row == {"code": "A", "capacity": 120}


def test_foreign_keys_are_enforced_not_merely_declared(conn: sqlite3.Connection) -> None:
    assert foreign_keys_are_on(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO run VALUES (1, 'ZZ', 1)")


def test_table_names_leaves_out_sqlite_internals(conn: sqlite3.Connection) -> None:
    assert table_names(conn) == ["lane", "run"]


def test_column_names_are_in_declaration_order(conn: sqlite3.Connection) -> None:
    assert column_names(conn, "run") == ["id", "lane", "position"]


def test_column_names_of_a_missing_table_is_a_config_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(ConfigError, match="no table named"):
        column_names(conn, "nope")


def test_a_transaction_commits_on_a_clean_exit(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        conn.execute("INSERT INTO lane VALUES ('B', 90)")
    assert conn.execute("SELECT COUNT(*) AS n FROM lane").fetchone()["n"] == 2


def test_a_transaction_rolls_back_everything_on_an_exception(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO lane VALUES ('B', 90)")
        conn.execute("INSERT INTO lane VALUES ('C', 90)")
        raise RuntimeError("the block died half way")
    assert conn.execute("SELECT COUNT(*) AS n FROM lane").fetchone()["n"] == 1


def test_nesting_a_transaction_is_refused_rather_than_flattened(
    conn: sqlite3.Connection,
) -> None:
    with transaction(conn), pytest.raises(Conflict, match="already open"), transaction(conn):
        pass


def test_a_rolled_back_transaction_leaves_the_connection_usable(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(RuntimeError), transaction(conn):
        raise RuntimeError("boom")
    with transaction(conn):
        conn.execute("INSERT INTO lane VALUES ('B', 90)")
    assert len(conn.execute("SELECT * FROM lane").fetchall()) == 2


def test_connect_makes_the_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "sale.sqlite3"
    connect(target).close()
    assert target.exists()


def test_a_read_only_caller_can_refuse_to_create(tmp_path: Path) -> None:
    target = tmp_path / "missing.sqlite3"
    with pytest.raises(ConfigError, match="no database at"):
        connect(target, create=False)
    assert not target.exists()


def test_connect_reopens_an_existing_file_with_its_rows(tmp_path: Path) -> None:
    target = tmp_path / "sale.sqlite3"
    first = connect(target)
    first.execute("CREATE TABLE lane (code TEXT PRIMARY KEY)")
    first.execute("INSERT INTO lane VALUES ('A')")
    first.close()
    second = connect(target, create=False)
    assert second.execute("SELECT code FROM lane").fetchone()["code"] == "A"
    second.close()


def test_a_healthy_database_reports_no_problems(conn: sqlite3.Connection) -> None:
    assert integrity_problems(conn) == []


def test_integrity_reports_orphans_left_by_a_disabled_key_check(
    conn: sqlite3.Connection,
) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO run VALUES (1, 'ZZ', 1)")
    conn.execute("PRAGMA foreign_keys = ON")
    problems = integrity_problems(conn)
    assert len(problems) == 1
    assert "foreign key violation in run" in problems[0]


def test_a_file_database_ends_up_in_wal(tmp_path: Path) -> None:
    target = tmp_path / "sale.sqlite3"
    first = connect(target)
    assert first.execute("PRAGMA journal_mode").fetchone()["journal_mode"] == "wal"
    first.close()
    second = connect(target, create=False)
    assert second.execute("PRAGMA journal_mode").fetchone()["journal_mode"] == "wal"
    second.close()


def test_an_in_memory_database_is_left_in_its_own_mode() -> None:
    conn = connect_memory()
    assert conn.execute("PRAGMA journal_mode").fetchone()["journal_mode"] == "memory"


def test_the_per_connection_pragmas_are_applied_every_time(tmp_path: Path) -> None:
    target = tmp_path / "sale.sqlite3"
    connect(target).close()
    conn = connect(target, create=False)
    assert foreign_keys_are_on(conn)
    assert conn.execute("PRAGMA busy_timeout").fetchone()["timeout"] > 0
    conn.close()
