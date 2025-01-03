import sqlite3
from pathlib import Path

import pytest

from autozest.errors import Conflict
from autozest.storage.db import connect
from autozest.storage.retry import (
    FIRST_BACKOFF,
    MAX_ATTEMPTS,
    backoff_for,
    is_busy,
    with_retry,
    writing,
)


def test_only_a_lock_message_counts_as_busy() -> None:
    assert is_busy(sqlite3.OperationalError("database is locked"))
    assert is_busy(sqlite3.OperationalError("database table is locked"))
    assert not is_busy(sqlite3.OperationalError("no such table: lane"))
    assert not is_busy(sqlite3.IntegrityError("UNIQUE constraint failed"))
    assert not is_busy(RuntimeError("database is locked"))


def test_the_backoff_doubles() -> None:
    assert backoff_for(1) == 0.0
    assert backoff_for(2) == FIRST_BACKOFF
    assert backoff_for(3) == FIRST_BACKOFF * 2
    assert backoff_for(5) == FIRST_BACKOFF * 8


def test_work_that_succeeds_runs_once() -> None:
    calls = []

    def work() -> str:
        calls.append(1)
        return "done"

    assert with_retry(work, sleep=lambda _: None) == "done"
    assert len(calls) == 1


def test_a_busy_database_is_retried_and_then_works() -> None:
    calls = []
    waits: list[float] = []

    def work() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "done"

    assert with_retry(work, sleep=waits.append) == "done"
    assert len(calls) == 3
    assert waits == [FIRST_BACKOFF, FIRST_BACKOFF * 2]


def test_something_that_is_not_contention_comes_straight_back() -> None:
    def work() -> str:
        raise sqlite3.OperationalError("no such table: lane")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with_retry(work, sleep=lambda _: None)


def test_a_constraint_failure_is_never_retried() -> None:
    calls = []

    def work() -> str:
        calls.append(1)
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    with pytest.raises(sqlite3.IntegrityError):
        with_retry(work, sleep=lambda _: None)
    assert len(calls) == 1


def test_giving_up_says_what_is_probably_wrong() -> None:
    def work() -> str:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(Conflict) as caught:
        with_retry(work, attempts=3, sleep=lambda _: None)
    assert "stayed locked through 3 attempts" in str(caught.value)
    assert "holding a transaction open" in str(caught.value)
    assert caught.value.context["attempts"] == 3


def test_the_default_is_five_goes() -> None:
    calls: list[int] = []

    def work() -> str:
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(Conflict):
        with_retry(work, sleep=lambda _: None)
    assert len(calls) == MAX_ATTEMPTS


def test_a_writing_block_commits(tmp_path: Path) -> None:
    conn = connect(tmp_path / "sale.sqlite3")
    conn.execute("CREATE TABLE lane (code TEXT PRIMARY KEY)")
    with writing(conn):
        conn.execute("INSERT INTO lane VALUES ('A')")
    assert conn.execute("SELECT COUNT(*) AS n FROM lane").fetchone()["n"] == 1
    conn.close()


def test_a_writing_block_rolls_back(tmp_path: Path) -> None:
    conn = connect(tmp_path / "sale.sqlite3")
    conn.execute("CREATE TABLE lane (code TEXT PRIMARY KEY)")
    with pytest.raises(RuntimeError), writing(conn):
        conn.execute("INSERT INTO lane VALUES ('A')")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) AS n FROM lane").fetchone()["n"] == 0
    conn.close()


def test_nesting_a_writing_block_is_refused(tmp_path: Path) -> None:
    conn = connect(tmp_path / "sale.sqlite3")
    with writing(conn), pytest.raises(Conflict, match="already open"), writing(conn):
        pass
    conn.close()
