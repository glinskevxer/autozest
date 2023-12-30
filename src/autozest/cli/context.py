"""What every command needs before it can do anything.

Click passes one of these down through the group. It holds the settings, opens
the database lazily so a command that only prints help does not create a file,
and gives every command the same clock so a test can freeze it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from autozest.config import Settings
from autozest.config import load as load_settings
from autozest.storage.db import connect
from autozest.storage.migrator import current_version, migrate


@dataclass
class Context:
    """The shared state of one invocation of the command line."""

    settings: Settings = field(default_factory=Settings)
    database: Path | None = None
    _conn: sqlite3.Connection | None = field(default=None, repr=False)

    @classmethod
    def build(cls, config_path: str | None, database: str | None) -> Context:
        settings = load_settings(config_path)
        target = Path(database) if database else settings.database_path()
        return cls(settings=settings, database=target)

    @property
    def path(self) -> Path:
        if self.database is None:
            raise RuntimeError("the context was built without a database path")
        return self.database

    def open(self, *, create: bool = False) -> sqlite3.Connection:
        """The connection, opened once and reused for the whole invocation."""
        if self._conn is None:
            self._conn = connect(self.path, create=create)
        return self._conn

    def initialise(self) -> int:
        """Create the database if needed, apply migrations, return the version."""
        conn = self.open(create=True)
        migrate(conn)
        return current_version(conn)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)
