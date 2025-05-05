import sqlite3

from autozest.tools.doctor import REPAIRS, SHAPE_CHECKS, examine, orphans, repair


def broken_entry(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'no lines', 'test', '2024-03-12T00:00:00+00:00')"
    )


def test_a_fresh_database_is_clean(db: sqlite3.Connection) -> None:
    report = examine(db)
    assert report.is_clean
    assert report.summary() == "nothing wrong"
    assert orphans(db) == []


def test_an_entry_with_no_lines_is_found_and_fixable(db: sqlite3.Connection) -> None:
    broken_entry(db)
    report = examine(db)
    assert not report.is_clean
    finding = report.findings[0]
    assert finding.code == "entry_without_lines"
    assert finding.repairable
    assert "fixable" in str(finding)


def test_repairing_removes_it(db: sqlite3.Connection) -> None:
    broken_entry(db)
    fixed = repair(db)
    assert fixed == {"entry_without_lines": 1}
    assert examine(db).is_clean


def test_an_unbalanced_entry_is_never_repaired(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'lopsided', 'test', '2024-03-12T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO ledger_line (entry_id, account, debit_cents) VALUES (1, 'cash', 100)"
    )
    report = examine(db)
    codes = [finding.code for finding in report.findings]
    assert "unbalanced_entry" in codes
    assert report.repairable == ()
    assert len(report.needs_a_person) == 1
    assert repair(db) == {}


def test_a_run_sold_with_no_price_is_for_a_person(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT INTO run (sale_code, lane, position, consignment_id, light, status) "
        "VALUES ('S-1', 'A', 1, 'C-1', 'green', 'sold')"
    )
    db.execute("PRAGMA foreign_keys = ON")
    report = examine(db)
    codes = [finding.code for finding in report.findings]
    assert "sold_without_price" in codes
    assert "sold_without_buyer" in codes
    assert all(not finding.repairable for finding in report.findings)


def test_orphans_are_named(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT INTO run (sale_code, lane, position, consignment_id, light, status) "
        "VALUES ('S-1', 'A', 1, 'C-1', 'green', 'scheduled')"
    )
    db.execute("PRAGMA foreign_keys = ON")
    found = orphans(db)
    assert found
    assert "run row" in found[0]


def test_every_repairable_check_has_a_repair() -> None:
    for code, _, _, repairable in SHAPE_CHECKS:
        if repairable:
            assert code in REPAIRS, code


def test_no_repair_exists_for_something_not_marked_repairable() -> None:
    repairable = {code for code, _, _, flag in SHAPE_CHECKS if flag}
    assert set(REPAIRS) <= repairable


def test_the_summary_counts_both_kinds(db: sqlite3.Connection) -> None:
    broken_entry(db)
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(
        "INSERT INTO run (sale_code, lane, position, consignment_id, light, status) "
        "VALUES ('S-1', 'A', 1, 'C-1', 'green', 'sold')"
    )
    db.execute("PRAGMA foreign_keys = ON")
    summary = examine(db).summary()
    assert "fixable" in summary
    assert "for a person" in summary
