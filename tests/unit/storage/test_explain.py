import sqlite3

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
