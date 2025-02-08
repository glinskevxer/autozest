"""The build tools, driven the way the workflow drives them."""

from __future__ import annotations

import sqlite3

import pytest

from autozest.tools import check_migrations, explain


def test_the_migration_checker_main_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert check_migrations.main() == 0
    assert "no problems" in capsys.readouterr().out


def test_the_checker_reports_a_table_with_no_primary_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = check_migrations.table_names

    def with_a_bad_table(conn: sqlite3.Connection) -> list[str]:
        conn.execute("CREATE TABLE IF NOT EXISTS loose (name TEXT)")
        return original(conn)

    monkeypatch.setattr(check_migrations, "table_names", with_a_bad_table)
    found = check_migrations.problems()
    assert any("has no primary key" in problem for problem in found)


def test_the_checker_main_exits_one_when_it_finds_something(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check_migrations, "problems", lambda: ["something is wrong"])
    assert check_migrations.main() == 1
    captured = capsys.readouterr()
    assert "something is wrong" in captured.err
    assert "1 problems" in captured.err


def test_the_explain_main_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert explain.main() == 0
    output = capsys.readouterr().out
    assert "seller statement" in output
    assert "without an index" in output


def test_a_column_that_is_not_snake_case_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = check_migrations.table_names

    def with_a_bad_column(conn: sqlite3.Connection) -> list[str]:
        conn.execute('CREATE TABLE IF NOT EXISTS odd (id INTEGER PRIMARY KEY, "Name" TEXT)')
        return original(conn)

    monkeypatch.setattr(check_migrations, "table_names", with_a_bad_column)
    found = check_migrations.problems()
    assert any("not snake case" in problem for problem in found)


def test_a_timestamp_stored_as_a_number_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = check_migrations.table_names

    def with_a_bad_timestamp(conn: sqlite3.Connection) -> list[str]:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS stamped (id INTEGER PRIMARY KEY, made_at INTEGER)"
        )
        return original(conn)

    monkeypatch.setattr(check_migrations, "table_names", with_a_bad_timestamp)
    found = check_migrations.problems()
    assert any("should be TEXT" in problem for problem in found)


def test_a_scan_shows_up_as_one() -> None:
    plan = explain.Plan(name="x", steps=("SCAN lane",))
    assert not plan.uses_an_index
    assert str(plan).startswith("scan")


def test_a_covering_index_counts_as_an_index() -> None:
    plan = explain.Plan(name="x", steps=("SEARCH lane USING COVERING INDEX lane_idx",))
    assert plan.uses_an_index
    assert plan.scans == ()
