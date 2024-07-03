"""Check the shipped migrations without touching a real database.

Run as ``python -m autozest.tools.check_migrations``. It is a build step, not
something an operator runs, and it exists because every schema mistake found in
review so far has been one of the same handful:

* a table with no primary key, which SQLite is perfectly happy with
* a foreign key pointing at a table spelled slightly differently
* a name that is not snake case, so half the queries reach for the wrong one
* a migration renumbered during a rebase

Applying the whole stack to a fresh in memory database catches the syntax. The
rest of the checks are the ones SQLite will not make for you.
"""

from __future__ import annotations

import re
import sqlite3
import sys

from autozest.storage.db import connect_memory, table_names
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
    conn.close()
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
