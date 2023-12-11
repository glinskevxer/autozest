"""Forward only schema migrations.

Numbered SQL files, applied in order, recorded with a checksum. There is no
down migration and there will not be one. A sale night that goes wrong is
recovered from the backup taken before the upgrade, not by reversing a script
against live money, and pretending otherwise makes people write reversals they
never test.

What the runner does insist on is that the files do not move under it. Every
applied migration's checksum is kept, and an edit to a file that has already run
somewhere stops the next upgrade rather than producing two databases that claim
the same version and do not have the same shape.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autozest.errors import ConfigError

MIGRATIONS_TABLE = "schema_migration"
_FILENAME = re.compile(r"^(?P<number>\d{3})_(?P<name>[a-z0-9_]+)\.sql$")

_BOOTSTRAP = f"""
CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
    number      INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TEXT NOT NULL
)
"""


@dataclass(frozen=True, order=True)
class Migration:
    """One numbered file on disk."""

    number: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()

    @property
    def filename(self) -> str:
        return f"{self.number:03d}_{self.name}.sql"


def default_directory() -> Path:
    return Path(__file__).parent / "migrations"


def load_migrations(directory: Path | None = None) -> list[Migration]:
    """Read every migration file, in number order, checking the set is sane."""
    folder = directory or default_directory()
    if not folder.is_dir():
        raise ConfigError(f"no migrations directory at {folder}", path=str(folder))

    found: dict[int, Migration] = {}
    for path in sorted(folder.iterdir()):
        if path.suffix != ".sql":
            continue
        match = _FILENAME.match(path.name)
        if match is None:
            raise ConfigError(
                f"{path.name!r} is not named like a migration, "
                "it should be three digits, an underscore and a lower case name",
                path=str(path),
            )
        number = int(match.group("number"))
        if number in found:
            raise ConfigError(
                f"two migrations are numbered {number:03d}",
                first=found[number].filename,
                second=path.name,
            )
        found[number] = Migration(number, match.group("name"), path.read_text("utf-8"))

    migrations = [found[key] for key in sorted(found)]
    _check_contiguous(migrations)
    return migrations


def _check_contiguous(migrations: list[Migration]) -> None:
    for position, migration in enumerate(migrations, start=1):
        if migration.number != position:
            raise ConfigError(
                f"the migrations skip a number, expected {position:03d} "
                f"and found {migration.number:03d}",
                filename=migration.filename,
            )


def applied(conn: sqlite3.Connection) -> dict[int, dict[str, str]]:
    """Everything already recorded in this database, keyed by number."""
    conn.execute(_BOOTSTRAP)
    rows = conn.execute(
        f"SELECT number, name, checksum, applied_at FROM {MIGRATIONS_TABLE} ORDER BY number"
    ).fetchall()
    return {row["number"]: row for row in rows}


def current_version(conn: sqlite3.Connection) -> int:
    """The highest applied number, or zero for an untouched database."""
    done = applied(conn)
    return max(done, default=0)


def pending(conn: sqlite3.Connection, directory: Path | None = None) -> list[Migration]:
    """Migrations on disk that this database has not run, checksums verified."""
    done = applied(conn)
    available = load_migrations(directory)
    verify(available, done)
    return [m for m in available if m.number not in done]


def verify(available: list[Migration], done: dict[int, dict[str, str]]) -> None:
    """Refuse to go on when an applied migration has been edited since."""
    by_number = {m.number: m for m in available}
    for number, row in sorted(done.items()):
        migration = by_number.get(number)
        if migration is None:
            raise ConfigError(
                f"migration {number:03d} has been applied to this database "
                "but its file is not on disk",
                name=row["name"],
            )
        if migration.checksum != row["checksum"]:
            raise ConfigError(
                f"migration {migration.filename} has changed since it was applied, "
                "the database and the code no longer agree",
                applied=row["checksum"][:12],
                on_disk=migration.checksum[:12],
            )


def _apply_one(conn: sqlite3.Connection, migration: Migration) -> None:
    """Run one migration and record it, all inside a single transaction.

    ``executescript`` commits whatever is open before it starts, so the BEGIN
    has to travel inside the script rather than around it. That is the whole
    reason this is not three lines in :func:`migrate`.
    """
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + migration.sql)
        conn.execute(
            f"INSERT INTO {MIGRATIONS_TABLE} (number, name, checksum, applied_at) "
            "VALUES (?, ?, ?, ?)",
            (
                migration.number,
                migration.name,
                migration.checksum,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection, directory: Path | None = None) -> list[Migration]:
    """Apply everything outstanding and return what was applied.

    Each migration is its own transaction. A failure part way leaves the
    database at the last migration that completed, which is a state the runner
    can be pointed at again once the file is fixed.
    """
    outstanding = pending(conn, directory)
    for migration in outstanding:
        _apply_one(conn, migration)
    return outstanding
