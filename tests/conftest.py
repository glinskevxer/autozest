"""Fixtures every layer of the suite shares."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from autozest.storage.db import connect_memory
from autozest.storage.migrator import migrate


@pytest.fixture()
def db() -> Iterator[sqlite3.Connection]:
    """A migrated in memory database, thrown away after each test."""
    conn = connect_memory()
    migrate(conn)
    yield conn
    conn.close()
