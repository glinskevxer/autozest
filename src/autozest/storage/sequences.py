"""Issuing the next reference of a kind.

Ten services had their own version of this and they were not the same. Four
counted every row in the table, which restarts nothing in January and issues a
duplicate the moment anything is deleted. Three counted rows matching the
year's prefix, which is right. Two read the highest sequence already used,
which is more right, because it survives a bulk import that inserted references
directly.

This is the third one. It reads the highest already issued for that prefix and
that year, so an import cannot collide with the next reference handed out, and
the sequence restarts every January the way the filing cabinets do.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from autozest.identifiers import parse_sequenced, sequenced
from autozest.storage.records import all_rows


def highest_used(conn: sqlite3.Connection, table: str, prefix: str, year: int) -> int:
    """The largest sequence already issued for this prefix and year."""
    rows = all_rows(
        conn, f"SELECT id FROM {table} WHERE id LIKE ?", (f"{prefix}-{year:04d}-%",)
    )
    highest = 0
    for row in rows:
        try:
            _, _, sequence = parse_sequenced(row["id"])
        except Exception:
            continue
        highest = max(highest, sequence)
    return highest


def next_reference(
    conn: sqlite3.Connection, table: str, prefix: str, *, on: date | None = None
) -> str:
    """The next reference of a kind, in the form ``C-2024-000412``."""
    day = on or date.today()
    return sequenced(prefix, day, highest_used(conn, table, prefix, day.year) + 1)
