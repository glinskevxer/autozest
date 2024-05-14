"""SQLite connections, configured the way this application needs them.

SQLite is the right store here. A sale is one building on one night, the write
volume is a few thousand rows, and the whole thing has to be copyable to a
laptop for the arbitration desk. What it is not is correct out of the box:
foreign keys are off, the default journal serialises readers behind the writer,
and a busy database raises instead of waiting. All three are fixed here so no
caller has to remember.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from autozest.errors import ConfigError, Conflict

#: How long a writer waits for the lock before giving up, in milliseconds.
BUSY_TIMEOUT_MS = 5_000

#: Applied on every connection. These are per connection settings and are not
#: remembered in the file.
PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA synchronous = NORMAL",
    f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}",
)

#: The journal mode is a property of the file, not of the connection, so it is
#: set once when the file is first opened and read back cheaply after that.
#: Setting it on every connect costs a write and a checkpoint, which is most of
#: the cost of opening a database that is already in the mode we want.
JOURNAL_MODE = "wal"


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(cursor.description, row, strict=True)}


def connect(path: Path | str, *, create: bool = True) -> sqlite3.Connection:
    """Open a database with the pragmas applied and rows coming back as dicts.

    ``create`` exists so a command that only reads fails loudly against a
    missing file rather than quietly creating an empty one and reporting that
    the sale has no consignments.
    """
    location = Path(path)
    if location.name != ":memory:":
        if not create and not location.exists():
            raise ConfigError(f"no database at {location}", path=str(location))
        location.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(location), isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES
    )
    conn.row_factory = _row_to_dict
    for pragma in PRAGMAS:
        conn.execute(pragma)
    _ensure_journal_mode(conn)
    return conn


def _ensure_journal_mode(conn: sqlite3.Connection) -> None:
    """Put the file in WAL, unless it is already there.

    An in memory database cannot be in WAL at all and says so by reporting
    ``memory``, which is not a failure and is left alone.
    """
    current = conn.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
    if current in (JOURNAL_MODE, "memory"):
        return
    conn.execute(f"PRAGMA journal_mode = {JOURNAL_MODE}")


def connect_memory() -> sqlite3.Connection:
    """An in memory database, for tests and for a dry run of an import."""
    return connect(":memory:")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one transaction, rolling back on any exception.

    Nesting is not supported and is not silently tolerated either. A nested
    call raises, because the inner block would otherwise commit work the outer
    one was still deciding about.
    """
    if conn.in_transaction:
        raise Conflict("a transaction is already open on this connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def table_names(conn: sqlite3.Connection) -> list[str]:
    """Every application table, sorted, with SQLite's own tables left out."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """The columns of a table in declaration order."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        raise ConfigError(f"no table named {table!r}", table=table)
    return [row["name"] for row in rows]


def foreign_keys_are_on(conn: sqlite3.Connection) -> bool:
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    return bool(row["foreign_keys"])


def integrity_problems(conn: sqlite3.Connection) -> list[str]:
    """Anything SQLite itself thinks is wrong with the file."""
    problems = [row["integrity_check"] for row in conn.execute("PRAGMA integrity_check")]
    if problems == ["ok"]:
        problems = []
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    problems.extend(
        f"foreign key violation in {row['table']} row {row['rowid']}" for row in violations
    )
    return problems
