"""A very small amount of shared machinery for the repositories.

Not an ORM. The queries are written out because the interesting ones are joins
across the block, the arbitration desk and settlement, and a mapper that hides
those makes them harder to read rather than easier. What is shared is the
boring part: turning a row into a dataclass, turning a dataclass into a row,
and raising the same NotFound everywhere.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

from autozest.errors import Conflict, NotFound

T = TypeVar("T")


def now_text() -> str:
    """The timestamp format every column in this schema uses."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def insert(conn: sqlite3.Connection, table: str, values: Mapping[str, Any]) -> None:
    """Insert one row, turning a unique clash into a Conflict.

    SQLite's own message names the index rather than the thing an operator
    typed, so it is rewritten here into something that can go on a screen.
    """
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    try:
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
        )
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "UNIQUE" in message:
            raise Conflict(f"that {table} already exists", table=table, detail=message) from exc
        raise


def update(
    conn: sqlite3.Connection, table: str, key: Mapping[str, Any], values: Mapping[str, Any]
) -> int:
    """Update matching rows and return how many changed."""
    assignments = ", ".join(f"{column} = ?" for column in values)
    condition = " AND ".join(f"{column} = ?" for column in key)
    cursor = conn.execute(
        f"UPDATE {table} SET {assignments} WHERE {condition}",
        (*values.values(), *key.values()),
    )
    return cursor.rowcount


def one(
    conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()
) -> dict[str, Any] | None:
    """The first row, or None."""
    row: dict[str, Any] | None = conn.execute(sql, tuple(params)).fetchone()
    return row


def one_or_raise(
    conn: sqlite3.Connection, sql: str, params: Iterable[Any], kind: str, key: object
) -> dict[str, Any]:
    """The first row, or a NotFound naming what was looked for."""
    row = one(conn, sql, params)
    if row is None:
        raise NotFound(kind, key)
    return row


def all_rows(
    conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = conn.execute(sql, tuple(params)).fetchall()
    return rows


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    """The first column of the first row. Used for counts and sums."""
    row = conn.execute(sql, tuple(params)).fetchone()
    if row is None:
        return None
    return next(iter(row.values()))


def exists(conn: sqlite3.Connection, table: str, **key: Any) -> bool:
    condition = " AND ".join(f"{column} = ?" for column in key)
    found = scalar(conn, f"SELECT 1 FROM {table} WHERE {condition} LIMIT 1", key.values())
    return found is not None
