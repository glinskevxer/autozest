"""Who did what.

Not a row level change log. That was the first design and it produced a table
bigger than the rest of the database inside a month, which nobody ever read.
What is kept is the sentence a manager actually asks for: who moved this unit's
floor, who decided this claim, who sent this title out.

The actor is whatever the command line was told, and it defaults to the system
user. That is not authentication and does not pretend to be. It is a name on a
line, which is what a small auction with eleven staff and one shared terminal
actually needs.
"""

from __future__ import annotations

import getpass
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from autozest.storage.records import all_rows, insert, now_text


@dataclass(frozen=True)
class AuditEvent:
    """One line of the trail."""

    id: int
    actor: str
    action: str
    subject: str
    subject_id: str
    made_at: datetime
    detail: str | None = None

    def __str__(self) -> str:
        tail = f"  {self.detail}" if self.detail else ""
        return (
            f"{self.made_at:%Y-%m-%d %H:%M}  {self.actor:<12} {self.action} "
            f"{self.subject} {self.subject_id}{tail}"
        )


def default_actor() -> str:
    """Whoever is at the terminal, as far as anyone can tell."""
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - only on a machine with no user
        return "unknown"


class AuditLog:
    def __init__(self, conn: sqlite3.Connection, actor: str | None = None) -> None:
        self.conn = conn
        self.actor = actor or default_actor()

    def record(
        self, action: str, subject: str, subject_id: str, detail: str | None = None
    ) -> None:
        insert(
            self.conn,
            "audit_event",
            {
                "actor": self.actor,
                "action": action,
                "subject": subject,
                "subject_id": subject_id,
                "detail": detail,
                "made_at": now_text(),
            },
        )

    @contextmanager
    def as_actor(self, actor: str) -> Iterator[AuditLog]:
        """Run a block under a different name, then put it back."""
        previous = self.actor
        self.actor = actor
        try:
            yield self
        finally:
            self.actor = previous

    def for_subject(self, subject: str, subject_id: str) -> list[AuditEvent]:
        return self._read(
            "SELECT * FROM audit_event WHERE subject = ? AND subject_id = ? ORDER BY id",
            (subject, subject_id),
        )

    def by_actor(self, actor: str, limit: int = 100) -> list[AuditEvent]:
        return self._read(
            "SELECT * FROM audit_event WHERE actor = ? ORDER BY id DESC LIMIT ?",
            (actor, limit),
        )

    def recent(self, limit: int = 50) -> list[AuditEvent]:
        return self._read("SELECT * FROM audit_event ORDER BY id DESC LIMIT ?", (limit,))

    def between(self, start: datetime, end: datetime) -> list[AuditEvent]:
        return self._read(
            "SELECT * FROM audit_event WHERE made_at >= ? AND made_at <= ? ORDER BY id",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        )

    def count(self) -> int:
        return len(self._read("SELECT * FROM audit_event", ()))

    def _read(self, sql: str, params: tuple[object, ...]) -> list[AuditEvent]:
        return [
            AuditEvent(
                id=row["id"],
                actor=row["actor"],
                action=row["action"],
                subject=row["subject"],
                subject_id=row["subject_id"],
                detail=row["detail"],
                made_at=datetime.fromisoformat(row["made_at"]),
            )
            for row in all_rows(self.conn, sql, params)
        ]
