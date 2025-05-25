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


def test_a_foreign_key_with_no_index_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = check_migrations.table_names

    def with_a_bare_key(conn: sqlite3.Connection) -> list[str]:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bare (id INTEGER PRIMARY KEY, "
            "vin TEXT REFERENCES vehicle (vin))"
        )
        return original(conn)

    monkeypatch.setattr(check_migrations, "table_names", with_a_bare_key)
    found = check_migrations.problems()
    assert any("no index behind it" in problem for problem in found)


def test_a_composite_key_covered_by_one_index_is_not_reported() -> None:
    """The first version of this checked columns one at a time.

    It reported run.lane and lane_assignment.lane, both of which are the second
    column of a perfectly good composite index, and cost an afternoon.
    """
    assert check_migrations.problems() == []


def test_an_index_named_after_another_table_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = check_migrations.table_names

    def with_a_misnamed_index(conn: sqlite3.Connection) -> list[str]:
        conn.execute("CREATE TABLE IF NOT EXISTS misnamed (id INTEGER PRIMARY KEY, x TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS elsewhere_idx ON misnamed (x)")
        return original(conn)

    monkeypatch.setattr(check_migrations, "table_names", with_a_misnamed_index)
    found = check_migrations.problems()
    assert any("is not named for it" in problem for problem in found)


def test_money_stored_as_text_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    original = check_migrations.table_names

    def with_text_money(conn: sqlite3.Connection) -> list[str]:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS loose_money (id INTEGER PRIMARY KEY, amount_cents TEXT)"
        )
        return original(conn)

    monkeypatch.setattr(check_migrations, "table_names", with_text_money)
    found = check_migrations.problems()
    assert any("should be INTEGER" in problem for problem in found)
