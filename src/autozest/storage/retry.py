"""Retrying a write that lost a race.

SQLite in WAL mode lets any number of readers run beside one writer, which is
exactly the shape of a sale night: the block writes, and the office, the
arbitration desk and two report screens read. What it does not allow is two
writers, and on a sale night there are three people at three terminals.

The busy timeout in the connection covers most of it. What it does not cover is
the case where the lock is taken between our statements rather than during one,
which surfaces as an immediate "database is locked" rather than a wait. That is
what this retries.

Retries back off, and they stop. A write still losing after five goes is not
contention, it is a connection somebody left a transaction open on, and
retrying forever turns that into a hung terminal instead of an error message.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

from autozest.errors import Conflict
from autozest.logs import log

T = TypeVar("T")

#: How many times a write is tried before it is called a real problem.
MAX_ATTEMPTS = 5

#: The first wait, in seconds. Each one after is twice the last.
FIRST_BACKOFF = 0.02

#: The sqlite messages that mean somebody else has the write lock.
BUSY_MESSAGES = ("database is locked", "database table is locked")


def is_busy(exc: BaseException) -> bool:
    """Whether a failure was contention rather than a mistake."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return any(fragment in message for fragment in BUSY_MESSAGES)


@dataclass(frozen=True)
class Attempt:
    """One try, for a caller that wants to know what happened."""

    number: int
    waited: float

    @property
    def is_first(self) -> bool:
        return self.number == 1


def backoff_for(attempt: int) -> float:
    """How long to wait before try number ``attempt``."""
    if attempt <= 1:
        return 0.0
    return float(FIRST_BACKOFF * (2 ** (attempt - 2)))


def with_retry(
    work: Callable[[], T],
    *,
    attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run something that writes, retrying while the database is busy.

    Anything that is not contention comes straight back out. A constraint
    violation retried five times is still a constraint violation and the delay
    only makes the error later.
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return work()
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            last = exc
            wait = backoff_for(attempt + 1)
            log.warning("storage.busy", attempt=attempt, waiting=f"{wait:.3f}")
            if attempt < attempts:
                sleep(wait)
    raise Conflict(
        f"the database stayed locked through {attempts} attempts, "
        "something is holding a transaction open",
        attempts=attempts,
        detail=str(last),
    )


@contextmanager
def writing(
    conn: sqlite3.Connection, *, attempts: int = MAX_ATTEMPTS
) -> Iterator[sqlite3.Connection]:
    """A transaction that retries the BEGIN while somebody else is writing.

    Only the BEGIN is retried. Once the block is running the work inside it is
    not safe to repeat, and a caller that wants the whole thing retried can
    wrap the call rather than the transaction.
    """
    if conn.in_transaction:
        raise Conflict("a transaction is already open on this connection")
    with_retry(lambda: conn.execute("BEGIN IMMEDIATE"), attempts=attempts)
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
