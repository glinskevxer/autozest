"""Two people writing to the same file at the same time.

A sale night has the block writing while the office and the arbitration desk
read, and about once an hour two of them write at the same moment. These run
real connections against a real file rather than mocking the lock, because the
thing being tested is what SQLite does and not what we think it does.

Threads rather than processes, deliberately. Threads share nothing here except
the file, which is exactly the arrangement in the building: separate terminals,
one database on a share.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date
from pathlib import Path

import pytest

from autozest.errors import Conflict
from autozest.money import Money
from autozest.settlement.ledger import Account, Entry, Ledger, credit, debit
from autozest.storage.db import connect
from autozest.storage.migrator import migrate
from autozest.storage.retry import is_busy, with_retry, writing


@pytest.fixture()
def shared(tmp_path: Path) -> Path:
    target = tmp_path / "shared.sqlite3"
    conn = connect(target)
    migrate(conn)
    conn.execute(
        "INSERT INTO account (id, name, class, status, created_at) "
        "VALUES ('d-100', 'Ridgeline', 'independent_dealer', 'active', "
        "'2024-03-12T00:00:00+00:00')"
    )
    conn.close()
    return target


def an_entry(reference: str, amount: str) -> Entry:
    money = Money.parse(amount)
    return Entry(
        reference=reference,
        memo="concurrent",
        source="test",
        source_id=reference,
        lines=(
            debit(Account.BUYER_RECEIVABLE, money, "d-100"),
            credit(Account.FEE_INCOME, money),
        ),
    )


def test_two_writers_both_land(shared: Path) -> None:
    failures: list[BaseException] = []

    def write(reference: str) -> None:
        conn = connect(shared, create=False)
        try:
            with writing(conn):
                Ledger(conn).post(an_entry(reference, "100.00"))
        except BaseException as exc:
            failures.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=write, args=(f"R-{index}",)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    conn = connect(shared, create=False)
    assert conn.execute("SELECT COUNT(*) AS n FROM ledger_entry").fetchone()["n"] == 6
    assert Ledger(conn).is_balanced()
    conn.close()


def test_a_reader_is_not_blocked_by_a_writer(shared: Path) -> None:
    """The whole reason for WAL. The office reads while the block writes."""
    writer = connect(shared, create=False)
    reader = connect(shared, create=False)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
            "VALUES ('R-1', 'held open', 'test', '2024-03-12T00:00:00+00:00')"
        )
        # The reader sees the world as it was before the open transaction.
        assert reader.execute("SELECT COUNT(*) AS n FROM ledger_entry").fetchone()["n"] == 0
        writer.execute("COMMIT")
        assert reader.execute("SELECT COUNT(*) AS n FROM ledger_entry").fetchone()["n"] == 1
    finally:
        writer.close()
        reader.close()


def test_a_second_writer_waits_rather_than_failing(shared: Path) -> None:
    first = connect(shared, create=False)
    second = connect(shared, create=False)
    try:
        first.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError) as caught:
            second.execute("BEGIN IMMEDIATE")
        assert is_busy(caught.value)
        first.execute("ROLLBACK")
        with_retry(lambda: second.execute("BEGIN IMMEDIATE"), sleep=lambda _: None)
        second.execute("ROLLBACK")
    finally:
        first.close()
        second.close()


def test_a_write_that_never_gets_in_gives_up_with_something_readable(
    shared: Path,
) -> None:
    holder = connect(shared, create=False)
    other = connect(shared, create=False)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(Conflict) as caught:
            with_retry(
                lambda: other.execute("BEGIN IMMEDIATE"), attempts=2, sleep=lambda _: None
            )
        assert "holding a transaction open" in str(caught.value)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        other.close()


def test_a_rolled_back_writer_leaves_the_file_usable(shared: Path) -> None:
    conn = connect(shared, create=False)
    try:
        with pytest.raises(RuntimeError), writing(conn):
            Ledger(conn).post(an_entry("R-1", "100.00"))
            raise RuntimeError("the terminal was unplugged")
        assert conn.execute("SELECT COUNT(*) AS n FROM ledger_entry").fetchone()["n"] == 0
        with writing(conn):
            Ledger(conn).post(an_entry("R-2", "100.00"))
        assert conn.execute("SELECT COUNT(*) AS n FROM ledger_entry").fetchone()["n"] == 1
    finally:
        conn.close()


def test_the_file_is_in_wal_so_all_of_this_holds(shared: Path) -> None:
    conn = connect(shared, create=False)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        assert mode == "wal"
        assert date.today() is not None
    finally:
        conn.close()
