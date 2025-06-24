import sqlite3

import pytest

from autozest.tools.explain import WATCHED, plans


def test_every_watched_query_has_a_plan(db: sqlite3.Connection) -> None:
    found = plans(db)
    assert len(found) == len(WATCHED)
    for plan in found:
        assert plan.steps


def test_the_queries_a_screen_waits_on_use_an_index(db: sqlite3.Connection) -> None:
    by_name = {plan.name: plan for plan in plans(db)}
    for name in (
        "seller statement",
        "aged receivables",
        "run sheet",
        "open claims",
        "outstanding titles",
        "payment by reference",
    ):
        assert by_name[name].uses_an_index, str(by_name[name])


def test_a_plan_describes_itself(db: sqlite3.Connection) -> None:
    plan = plans(db)[0]
    text = str(plan)
    assert plan.name in text
    assert text.startswith("index") or text.startswith("scan")


def test_the_module_builds_its_own_database_when_given_none() -> None:
    assert len(plans()) == len(WATCHED)


def test_a_scan_is_reported_rather_than_failed(db: sqlite3.Connection) -> None:
    from autozest.tools.explain import Plan

    scanning = Plan(name="x", steps=("SCAN lane",))
    assert not scanning.uses_an_index
    assert scanning.scans == ("SCAN lane",)


def test_plans_closes_the_connection_it_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python 3.13 raises a ResourceWarning when an unclosed connection is
    collected, and with warnings as errors that surfaced as three unrelated
    tests failing on whichever one happened to trigger the collection.

    Asserted by watching the close rather than by the warning, because 3.12
    does not emit it and the test has to hold on both.
    """
    from autozest.tools import explain as module

    made: list[sqlite3.Connection] = []
    real = module.connect_memory

    def watched() -> sqlite3.Connection:
        conn = real()
        made.append(conn)
        return conn

    monkeypatch.setattr(module, "connect_memory", watched)
    module.plans()

    assert len(made) == 1
    # A closed sqlite3 connection refuses to be used, which is the only way to
    # ask one whether it is closed. sqlite3.Connection is a C type and will not
    # take a patched close().
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        made[0].execute("SELECT 1")


def test_plans_does_not_close_a_connection_it_was_given(
    db: sqlite3.Connection,
) -> None:
    """A caller's connection is the caller's to close."""
    plans(db)
    assert db.execute("SELECT 1 AS n").fetchone()["n"] == 1
