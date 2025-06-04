from __future__ import annotations

from pathlib import Path

from tests.cli.conftest import Invoke


def test_check_is_quiet_on_a_fresh_database(initialised: Invoke) -> None:
    assert "0 problems" in initialised("admin", "check").output


def test_stats_lists_the_tables(initialised: Invoke) -> None:
    output = initialised("admin", "stats").output
    assert "consignment" in output
    assert "ledger_entry" in output
    assert "schema" in output


def test_a_backup_lands_where_it_was_asked_to(initialised: Invoke, tmp_path: Path) -> None:
    target = tmp_path / "backups" / "copy.sqlite3"
    result = initialised("admin", "backup", str(target)).output
    assert str(target) in result
    assert target.exists()
    assert target.stat().st_size > 0


def test_a_backup_with_no_target_names_itself(initialised: Invoke, db_path: Path) -> None:
    initialised("admin", "backup")
    made = list(db_path.parent.glob("sale-*.sqlite3"))
    assert len(made) == 1


def test_the_backup_is_usable(initialised: Invoke, tmp_path: Path) -> None:
    target = tmp_path / "copy.sqlite3"
    initialised("admin", "backup", str(target))
    from autozest.storage.db import connect, table_names

    conn = connect(target, create=False)
    assert "consignment" in table_names(conn)
    conn.close()


def test_vacuum_reports_both_sizes(initialised: Invoke) -> None:
    assert "bytes to" in initialised("admin", "vacuum").output


def test_the_audit_trail_starts_empty(initialised: Invoke) -> None:
    assert "0 events of 0" in initialised("admin", "audit").output


def test_a_note_lands_on_the_trail(initialised: Invoke) -> None:
    initialised(
        "admin",
        "note",
        "consignment",
        "C-2024-000412",
        "seller rang about the floor",
        "--actor",
        "j.hale",
    )
    output = initialised("admin", "audit").output
    assert "j.hale" in output
    assert "seller rang" in output
    assert "1 events of 1" in output


def test_narrowing_the_trail(initialised: Invoke) -> None:
    initialised("admin", "note", "sale", "S-1", "first", "--actor", "j.hale")
    initialised("admin", "note", "sale", "S-2", "second", "--actor", "c.mott")
    assert "first" in initialised("admin", "audit", "--actor", "j.hale").output
    assert "second" not in initialised("admin", "audit", "--actor", "j.hale").output
    subject = initialised("admin", "audit", "--subject", "sale", "--id", "S-2").output
    assert "second" in subject
    assert "first" not in subject


def test_seeding_builds_a_sale(initialised: Invoke) -> None:
    output = initialised("admin", "seed", "--units", "10").output
    assert "S-20240312" in output
    assert "10 units" in output
    assert "sold" in output


def test_seeding_twice_is_refused(initialised: Invoke) -> None:
    initialised("admin", "seed", "--units", "4")
    initialised("admin", "seed", "--units", "4", expect=5)


def test_a_seeded_database_still_passes_its_own_checks(initialised: Invoke) -> None:
    initialised("admin", "seed", "--units", "10")
    assert "0 problems" in initialised("admin", "check").output


def test_a_seeded_sale_recaps(initialised: Invoke) -> None:
    initialised("admin", "seed", "--units", "12")
    assert "sale recap" in initialised("report", "recap", "S-20240312").output


def test_health_on_a_fresh_database(initialised: Invoke) -> None:
    output = initialised("admin", "health").output
    assert "healthy" in output
    assert "ok   ledger" in output


def test_health_can_hide_the_quiet_checks(initialised: Invoke) -> None:
    output = initialised("admin", "health", "--no-soft").output
    assert "calendar" not in output


def test_the_config_command_prints_what_loaded(initialised: Invoke) -> None:
    output = initialised("admin", "config").output
    assert "Autozest Remarketing" in output
    assert "opens" in output


def test_retention_is_a_dry_run_by_default(initialised: Invoke) -> None:
    output = initialised("admin", "retention").output
    assert "would remove 0 rows" in output
    assert "audit_event" in output


def test_retention_can_be_applied(initialised: Invoke) -> None:
    assert "removed 0 rows" in initialised("admin", "retention", "--apply").output


def test_archiving_a_seeded_sale(initialised: Invoke, tmp_path: Path) -> None:
    initialised("admin", "seed", "--units", "6")
    initialised("sale", "close", "S-20240312")
    target = tmp_path / "S-20240312.sqlite3"
    output = initialised("admin", "archive", "S-20240312", str(target)).output
    assert "rows to" in output
    assert target.exists()


def test_archiving_an_open_sale_exits_seven(initialised: Invoke, tmp_path: Path) -> None:
    initialised("admin", "seed", "--units", "4")
    initialised("admin", "archive", "S-20240312", str(tmp_path / "a.sqlite3"), expect=7)


def test_sizes_ranks_the_tables(initialised: Invoke) -> None:
    initialised("admin", "seed", "--units", "8")
    output = initialised("admin", "sizes", "--top", "5").output
    assert "rows" in output
    assert "tables," in output


def test_sizes_counts_only_the_migration_rows_on_a_fresh_database(
    initialised: Invoke,
) -> None:
    from autozest.storage.migrator import load_migrations

    output = initialised("admin", "sizes").output
    assert "schema_migration" in output
    assert "rows" in output
    assert f" {len(load_migrations())} rows" in output


def test_a_writing_command_lands_on_the_trail(initialised: Invoke) -> None:
    initialised("sale", "create", "2024-03-12", "--title", "Tuesday")
    output = initialised("admin", "audit").output
    assert "sale.create" in output
    assert "2024-03-12" in output


def test_a_reading_command_does_not(initialised: Invoke) -> None:
    initialised("sale", "create", "2024-03-12", "--title", "Tuesday")
    initialised("sale", "list")
    initialised("report", "reference")
    output = initialised("admin", "audit").output
    assert "sale.list" not in output
    assert "report." not in output
    assert "1 events of 1" in output


def test_a_command_that_failed_leaves_no_line(initialised: Invoke) -> None:
    result = initialised("sale", "create", "not-a-date", "--title", "Tuesday", expect=2)
    assert "is not a date" in result.output
    assert "0 events" in initialised("admin", "audit").output


def test_the_query_plans_are_reported(initialised: Invoke) -> None:
    output = initialised("admin", "plans").output
    assert "seller statement" in output
    assert "0 without an index" in output


def test_a_redaction_dry_run_writes_nothing(initialised: Invoke) -> None:
    initialised("admin", "seed", "--units", "4")
    output = initialised("admin", "redact").output
    assert "account.name" in output
    assert "values across" in output


def test_a_redacted_copy_is_written(initialised: Invoke, tmp_path: Path) -> None:
    initialised("admin", "seed", "--units", "4")
    target = tmp_path / "shareable.sqlite3"
    output = initialised("admin", "redact", str(target)).output
    assert "written to" in output
    assert target.exists()


def test_the_manual_prints(initialised: Invoke) -> None:
    output = initialised("admin", "manual").output
    assert "az consign register" in output
    assert "Exit statuses" in output


def test_the_manual_writes_to_a_file(initialised: Invoke, tmp_path: Path) -> None:
    target = tmp_path / "manual.txt"
    result = initialised("admin", "manual", "--out", str(target)).output
    assert "lines to" in result
    assert "az settle post" in target.read_text()


def test_the_doctor_is_quiet_on_a_fresh_database(initialised: Invoke) -> None:
    assert "nothing wrong" in initialised("admin", "doctor").output


def test_the_doctor_exits_non_zero_when_a_person_is_needed(
    initialised: Invoke, db_path: Path
) -> None:
    from autozest.storage.db import connect

    conn = connect(db_path)
    conn.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'lopsided', 'test', '2024-03-12T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO ledger_line (entry_id, account, debit_cents) VALUES (1, 'cash', 100)"
    )
    conn.close()
    result = initialised("admin", "doctor", expect=1)
    assert "unbalanced_entry" in result.output
    assert "for a person" in result.output


def test_the_history_prints(initialised: Invoke) -> None:
    output = initialised("admin", "history").output
    assert "0.1.0" in output
    assert "this build is" in output


def test_breaking_changes_since_a_version(initialised: Invoke) -> None:
    output = initialised("admin", "history", "--since", "0.0.1").output
    assert "0 breaking changes since 0.0.1" in output


def test_verify_passes_on_a_fresh_database(initialised: Invoke) -> None:
    output = initialised("admin", "verify").output
    assert "everything passes" in output
    assert "health/" in output
    assert "release/" in output


def test_verify_can_print_only_what_failed(initialised: Invoke) -> None:
    output = initialised("admin", "verify", "--quiet").output
    assert "ok  " not in output
    assert "everything passes" in output


def test_verify_exits_non_zero_when_the_building_cannot_run(
    initialised: Invoke, db_path: Path
) -> None:
    from autozest.storage.db import connect

    conn = connect(db_path)
    conn.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'lopsided', 'test', '2024-03-12T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO ledger_line (entry_id, account, debit_cents) VALUES (1, 'cash', 100)"
    )
    conn.close()
    result = initialised("admin", "verify", expect=1)
    assert "STOP" in result.output
