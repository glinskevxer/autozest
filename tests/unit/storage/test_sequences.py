import sqlite3
from datetime import date

from autozest.storage.sequences import highest_used, next_reference


def rows(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Put references straight into the notice table, the way an import would."""
    conn.execute(
        "INSERT OR IGNORE INTO account (id, name, class, status, created_at) "
        "VALUES ('p-1', 'Walk In', 'public', 'active', '2024-03-12T00:00:00+00:00')"
    )
    for reference in ids:
        conn.execute(
            "INSERT INTO notice (id, kind, party_id, subject, body, channel, status, "
            "queued_at) VALUES (?, 'x', 'p-1', 's', 'b', 'email', 'queued', ?)",
            (reference, "2024-03-12T00:00:00+00:00"),
        )


def test_the_first_of_a_year(db: sqlite3.Connection) -> None:
    assert next_reference(db, "notice", "N", on=date(2024, 3, 12)) == "N-2024-000001"


def test_it_follows_the_highest_used_not_the_count(db: sqlite3.Connection) -> None:
    rows(db, ["N-2024-000007", "N-2024-000002"])
    assert highest_used(db, "notice", "N", 2024) == 7
    assert next_reference(db, "notice", "N", on=date(2024, 3, 12)) == "N-2024-000008"


def test_a_deleted_row_does_not_cause_a_collision(db: sqlite3.Connection) -> None:
    rows(db, ["N-2024-000001", "N-2024-000002"])
    db.execute("DELETE FROM notice WHERE id = 'N-2024-000001'")
    assert next_reference(db, "notice", "N", on=date(2024, 3, 12)) == "N-2024-000003"


def test_the_sequence_restarts_every_january(db: sqlite3.Connection) -> None:
    rows(db, ["N-2024-000412"])
    assert next_reference(db, "notice", "N", on=date(2025, 1, 2)) == "N-2025-000001"


def test_another_prefix_in_the_same_table_is_ignored(db: sqlite3.Connection) -> None:
    rows(db, ["Q-2024-000009"])
    assert next_reference(db, "notice", "N", on=date(2024, 3, 12)) == "N-2024-000001"


def test_a_hand_written_id_is_skipped_rather_than_fatal(db: sqlite3.Connection) -> None:
    rows(db, ["N-2024-000003", "N-2024-legacy"])
    assert next_reference(db, "notice", "N", on=date(2024, 3, 12)) == "N-2024-000004"
