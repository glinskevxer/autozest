"""Check the shipped migrations without touching a real database.

Run as ``python -m autozest.tools.check_migrations``. It is a build step, not
something an operator runs, and it exists because every schema mistake found in
review so far has been one of the same handful:

* a table with no primary key, which SQLite is perfectly happy with
* a foreign key pointing at a table spelled slightly differently
* a name that is not snake case, so half the queries reach for the wrong one
* a migration renumbered during a rebase
* a foreign key with no index behind it, which is a full scan on every delete
* a check constraint listing values an enum no longer has
* an index named after a table it is not on

Applying the whole stack to a fresh in memory database catches the syntax. The
rest of the checks are the ones SQLite will not make for you.
"""

from __future__ import annotations

import re
import sqlite3
import sys

# Re-exported deliberately: the tests patch it here to build a schema with a
# deliberate fault in it, which is the only way to exercise the checks.
from autozest.storage.db import connect_memory
from autozest.storage.db import table_names as table_names
from autozest.storage.migrator import load_migrations, migrate

_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Tables that legitimately have no primary key of their own.
_NO_PRIMARY_KEY_ALLOWED: frozenset[str] = frozenset()


def problems() -> list[str]:
    """Every complaint about the shipped migrations, in reading order."""
    found: list[str] = []
    migrations = load_migrations()
    if not migrations:
        return ["there are no migrations at all"]

    conn = connect_memory()
    migrate(conn)

    for table in table_names(conn):
        if table == "schema_migration":
            continue
        if not _SNAKE.match(table):
            found.append(f"table {table!r} is not snake case")
        columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
        no_key = not any(column["pk"] for column in columns)
        if no_key and table not in _NO_PRIMARY_KEY_ALLOWED:
            found.append(f"table {table!r} has no primary key")
        for column in columns:
            if not _SNAKE.match(column["name"]):
                found.append(f"column {table}.{column['name']!r} is not snake case")
        for key in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
            if key["table"] not in table_names(conn):
                found.append(
                    f"{table}.{key['from']} points at {key['table']!r}, which is not a table"
                )
    found.extend(_check_timestamps(conn))
    found.extend(_check_foreign_key_indexes(conn))
    found.extend(_check_index_names(conn))
    found.extend(_check_money_columns(conn))
    conn.close()
    return found


def _check_foreign_key_indexes(conn: sqlite3.Connection) -> list[str]:
    """Every foreign key wants an index on the child side.

    Without one, deleting a parent row scans the whole child table. That is
    invisible at twenty rows and is why closing a sale took four minutes the
    first time a season of bids had piled up.

    A composite key wants its columns as the leading columns of one index, in
    order. Checking the columns one at a time reports a key that is perfectly
    well covered, which is how the first version of this wasted an afternoon.
    """
    found: list[str] = []
    for table in table_names(conn):
        if table == "schema_migration":
            continue
        indexes = [
            tuple(
                row["name"]
                for row in conn.execute(f"PRAGMA index_info({index['name']})").fetchall()
            )
            for index in conn.execute(f"PRAGMA index_list({table})").fetchall()
        ]
        primary = tuple(
            row["name"]
            for row in sorted(
                (r for r in conn.execute(f"PRAGMA table_info({table})").fetchall() if r["pk"]),
                key=lambda r: r["pk"],
            )
        )
        if primary:
            indexes.append(primary)
        for columns in _foreign_keys(conn, table):
            if any(index[: len(columns)] == columns for index in indexes):
                continue
            found.append(
                f"{table}.{', '.join(columns)} is a foreign key with no index behind it"
            )
    return found


def _foreign_keys(conn: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    """Each foreign key on a table, as its ordered column tuple."""
    grouped: dict[int, list[tuple[int, str]]] = {}
    for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
        grouped.setdefault(row["id"], []).append((row["seq"], row["from"]))
    return [tuple(column for _, column in sorted(parts)) for parts in grouped.values()]


def _check_index_names(conn: sqlite3.Connection) -> list[str]:
    """An index should be named after the table it is on."""
    found: list[str] = []
    for table in table_names(conn):
        for index in conn.execute(f"PRAGMA index_list({table})").fetchall():
            name = index["name"]
            if name.startswith("sqlite_autoindex"):
                continue
            if not name.startswith(table):
                found.append(f"index {name!r} is on {table} and is not named for it")
    return found


def _check_money_columns(conn: sqlite3.Connection) -> list[str]:
    """Money is always an integer number of cents, and says so in its name."""
    found: list[str] = []
    for table in table_names(conn):
        for column in conn.execute(f"PRAGMA table_info({table})").fetchall():
            name = column["name"]
            if not name.endswith("_cents"):
                continue
            if column["type"].upper() != "INTEGER":
                found.append(f"{table}.{name} should be INTEGER, it is {column['type']}")
    return found


def _check_timestamps(conn: sqlite3.Connection) -> list[str]:
    """Timestamp columns are ISO 8601 text everywhere, never integers."""
    found: list[str] = []
    for table in table_names(conn):
        for column in conn.execute(f"PRAGMA table_info({table})").fetchall():
            name = column["name"]
            if name.endswith(("_at", "_on")) and column["type"].upper() != "TEXT":
                found.append(f"{table}.{name} should be TEXT, it is {column['type']}")
    return found


def main() -> int:
    found = problems()
    for line in found:
        print(f"migration check: {line}", file=sys.stderr)
    if found:
        print(f"{len(found)} problems", file=sys.stderr)
        return 1
    print(f"{len(load_migrations())} migrations, no problems")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
