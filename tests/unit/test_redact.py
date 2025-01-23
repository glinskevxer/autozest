import sqlite3
from pathlib import Path

from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.storage.db import connect
from autozest.tools.redact import KEPT, pseudonym, redact, would_change


def stocked(db: sqlite3.Connection) -> None:
    AccountRepository(db).add(
        Account(
            id="d-100",
            name="Ridgeline Motors",
            account_class=AccountClass.INDEPENDENT_DEALER,
            dealer_licence="TX-44192",
            tax_id="99-1234567",
            state="TX",
        )
    )


def test_a_pseudonym_is_stable_within_a_salt() -> None:
    assert pseudonym("Ridgeline Motors", "a") == pseudonym("Ridgeline Motors", "a")
    assert pseudonym("Ridgeline Motors", "a") != pseudonym("Ridgeline Motors", "b")
    assert pseudonym("Ridgeline Motors", "a") != pseudonym("Kestrel Auto", "a")
    assert pseudonym("x", "a").startswith("redacted-")


def test_nothing_to_redact_on_an_empty_database(db: sqlite3.Connection) -> None:
    assert would_change(db) == {}


def test_the_identifying_columns_are_counted(db: sqlite3.Connection) -> None:
    stocked(db)
    counts = would_change(db)
    assert counts["account.name"] == 1
    assert counts["account.tax_id"] == 1
    assert counts["account.dealer_licence"] == 1


def test_a_redacted_copy_loses_the_names(db: sqlite3.Connection, tmp_path: Path) -> None:
    stocked(db)
    target = tmp_path / "copy.sqlite3"
    result = redact(db, target)
    assert result.rows == 3
    copy = connect(target, create=False)
    row = copy.execute("SELECT * FROM account WHERE id = 'd-100'").fetchone()
    assert row["name"].startswith("redacted-")
    assert row["tax_id"].startswith("redacted-")
    copy.close()


def test_the_keys_are_kept_so_the_copy_still_works(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    stocked(db)
    target = tmp_path / "copy.sqlite3"
    redact(db, target)
    copy = connect(target, create=False)
    row = copy.execute("SELECT * FROM account WHERE id = 'd-100'").fetchone()
    assert row["id"] == "d-100"
    assert row["class"] == "independent_dealer"
    assert row["state"] == "TX"
    copy.close()
    assert "id" in KEPT
    assert "vin" in KEPT


def test_the_original_is_untouched(db: sqlite3.Connection, tmp_path: Path) -> None:
    stocked(db)
    redact(db, tmp_path / "copy.sqlite3")
    row = db.execute("SELECT name FROM account WHERE id = 'd-100'").fetchone()
    assert row["name"] == "Ridgeline Motors"


def test_two_redactions_with_different_salts_do_not_line_up(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    stocked(db)
    first = tmp_path / "one.sqlite3"
    second = tmp_path / "two.sqlite3"
    redact(db, first, salt="one")
    redact(db, second, salt="two")
    left = connect(first, create=False).execute("SELECT name FROM account").fetchone()
    right = connect(second, create=False).execute("SELECT name FROM account").fetchone()
    assert left["name"] != right["name"]


def test_money_is_never_touched(db: sqlite3.Connection, tmp_path: Path) -> None:
    stocked(db)
    db.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'y', 'test', '2024-03-12T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO ledger_line (entry_id, account, debit_cents) VALUES (1, 'cash', 8200)"
    )
    target = tmp_path / "copy.sqlite3"
    redact(db, target)
    copy = connect(target, create=False)
    assert copy.execute("SELECT debit_cents AS n FROM ledger_line").fetchone()["n"] == 8200
    copy.close()
