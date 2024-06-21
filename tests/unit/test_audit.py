import sqlite3
from datetime import UTC, datetime, timedelta

from autozest.audit import AuditLog, default_actor
from autozest.storage.db import table_names


def test_the_audit_table_is_there(db: sqlite3.Connection) -> None:
    assert "audit_event" in table_names(db)


def test_an_empty_log(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    assert log.recent() == []
    assert log.count() == 0


def test_recording_and_reading_back(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    log.record("floor.moved", "consignment", "C-2024-000412", "4500 to 3900")
    events = log.for_subject("consignment", "C-2024-000412")
    assert len(events) == 1
    assert events[0].actor == "j.hale"
    assert events[0].action == "floor.moved"
    assert events[0].detail == "4500 to 3900"


def test_an_event_reads_as_a_sentence(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    log.record("title.sent", "consignment", "C-2024-000412", "to the buyer")
    text = str(log.recent()[0])
    assert "j.hale" in text
    assert "title.sent" in text
    assert "to the buyer" in text


def test_an_event_with_no_detail_does_not_trail_whitespace(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    log.record("sale.opened", "sale", "S-20240312")
    assert not str(log.recent()[0]).endswith("  ")


def test_the_subject_trail_is_in_order(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    for action in ("registered", "arrived", "inspected"):
        log.record(action, "consignment", "C-2024-000412")
    assert [e.action for e in log.for_subject("consignment", "C-2024-000412")] == [
        "registered",
        "arrived",
        "inspected",
    ]


def test_recent_comes_back_newest_first(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    log.record("first", "sale", "S-1")
    log.record("second", "sale", "S-2")
    assert [e.action for e in log.recent()] == ["second", "first"]


def test_narrowing_to_one_actor(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    log.record("first", "sale", "S-1")
    with log.as_actor("c.mott"):
        log.record("second", "sale", "S-2")
    log.record("third", "sale", "S-3")
    assert [e.action for e in log.by_actor("j.hale")] == ["third", "first"]
    assert [e.action for e in log.by_actor("c.mott")] == ["second"]


def test_the_actor_goes_back_after_the_block(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    with log.as_actor("c.mott"):
        assert log.actor == "c.mott"
    assert log.actor == "j.hale"


def test_the_actor_goes_back_even_when_the_block_raises(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    try:
        with log.as_actor("c.mott"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert log.actor == "j.hale"


def test_a_window_of_time(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    log.record("first", "sale", "S-1")
    now = datetime.now(UTC)
    assert len(log.between(now - timedelta(hours=1), now + timedelta(hours=1))) == 1
    assert log.between(now + timedelta(hours=1), now + timedelta(hours=2)) == []


def test_the_default_actor_is_whoever_is_at_the_terminal(db: sqlite3.Connection) -> None:
    assert default_actor()
    assert AuditLog(db).actor == default_actor()


def test_counting(db: sqlite3.Connection) -> None:
    log = AuditLog(db, "j.hale")
    log.record("first", "sale", "S-1")
    log.record("second", "sale", "S-2")
    assert log.count() == 2
