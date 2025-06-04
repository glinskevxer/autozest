import sqlite3

from autozest.verify import Level, render, verify


def test_a_fresh_database_passes_everything(db: sqlite3.Connection) -> None:
    result = verify(db)
    assert result.is_usable
    assert result.failures == ()
    assert "everything passes" in result.summary()


def test_every_check_names_where_it_came_from(db: sqlite3.Connection) -> None:
    sources = {item.source for item in verify(db).results}
    assert {"health", "doctor", "release", "advisories"} <= sources


def test_a_shape_problem_that_can_be_fixed_is_only_worth_a_look(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'no lines', 'test', '2024-03-12T00:00:00+00:00')"
    )
    result = verify(db)
    assert result.is_usable
    assert len(result.advisory) == 1
    assert result.advisory[0].level is Level.LOOK
    assert "look" in str(result.advisory[0])


def test_an_unbalanced_ledger_stops_the_building(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO ledger_entry (reference, memo, source, posted_at) "
        "VALUES ('x', 'lopsided', 'test', '2024-03-12T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO ledger_line (entry_id, account, debit_cents) VALUES (1, 'cash', 100)"
    )
    result = verify(db)
    assert not result.is_usable
    assert any(item.level is Level.STOP for item in result.blocking)
    assert "stopping" in result.summary()
    assert any("STOP" in str(item) for item in result.blocking)


def test_the_levels_say_who_gets_woken_up() -> None:
    assert Level.STOP.wakes_somebody
    assert not Level.LOOK.wakes_somebody


def test_the_rendered_run_ends_with_the_summary(db: sqlite3.Connection) -> None:
    lines = render(db)
    assert lines[-1].endswith("everything passes")
    assert any(line.startswith("ok") for line in lines)
