"""What the building has to tell somebody, written down.

Deliberately a queue rather than a send. This system has no mail server and
should not have one: it writes down what needs saying and something else reads
the queue and sends it. A failed send is then somebody else's retry, and a sale
night is never held up by a mail relay.

Notices are built from templates that live here rather than in the modules that
raise them, for one reason: a seller gets four different letters from four
different desks and they should sound like they came from one business. Keeping
the wording together is the only way that happens.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from autozest.errors import NotFound, ValidationError
from autozest.money import Money
from autozest.storage.records import all_rows, insert, now_text, one, update
from autozest.storage.sequences import next_reference

NOTICE_PREFIX = "N"

#: How many times a sender should try before somebody looks at it.
MAX_ATTEMPTS = 3


class Channel(Enum):
    EMAIL = "email"
    POST = "post"
    SMS = "sms"
    PORTAL = "portal"

    @property
    def is_immediate(self) -> bool:
        return self in (Channel.EMAIL, Channel.SMS, Channel.PORTAL)


class NoticeStatus(Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_finished(self) -> bool:
        return self in (NoticeStatus.SENT, NoticeStatus.CANCELLED)


@dataclass(frozen=True)
class Notice:
    """One thing to say to one party."""

    id: str
    kind: str
    party_id: str
    subject: str
    body: str
    channel: Channel = Channel.EMAIL
    status: NoticeStatus = NoticeStatus.QUEUED
    reference: str | None = None
    queued_at: datetime | None = None
    sent_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None

    @property
    def is_stuck(self) -> bool:
        return self.status is NoticeStatus.FAILED and self.attempts >= MAX_ATTEMPTS

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Notice:
        return cls(
            id=row["id"],
            kind=row["kind"],
            party_id=row["party_id"],
            subject=row["subject"],
            body=row["body"],
            channel=Channel(row["channel"]),
            status=NoticeStatus(row["status"]),
            reference=row["reference"],
            queued_at=datetime.fromisoformat(row["queued_at"]),
            sent_at=datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
            attempts=row["attempts"],
            last_error=row["last_error"],
        )


def title_chase(consignment_id: str, days_waiting: int) -> tuple[str, str]:
    return (
        f"Title outstanding on {consignment_id}",
        f"We are still waiting on the title for {consignment_id}, now {days_waiting} days "
        "after the sale. The buyer's money is with us and the seller's proceeds are held "
        "until it arrives. Please send it or tell us where it is.",
    )


def claim_decided(claim_id: str, outcome: str, amount: Money) -> tuple[str, str]:
    return (
        f"Arbitration {claim_id} decided",
        f"Claim {claim_id} has been decided as {outcome} at {amount}. You have three "
        "business days to appeal if you think that is wrong.",
    )


def payment_due(balance: Money, due_on: date) -> tuple[str, str]:
    return (
        f"{balance} due by {due_on:%d %B}",
        f"Your account stands at {balance}, due by {due_on:%d %B}. A late fee applies "
        "after that and the titles on anything unpaid stay with us.",
    )


def proceeds_released(consignment_id: str, amount: Money) -> tuple[str, str]:
    return (
        f"Proceeds released on {consignment_id}",
        f"{amount} has been released for {consignment_id} and is on its way to you.",
    )


class NoticeQueue:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def queue(
        self,
        kind: str,
        party_id: str,
        subject: str,
        body: str,
        *,
        channel: Channel = Channel.EMAIL,
        reference: str | None = None,
    ) -> Notice:
        if not subject.strip() or not body.strip():
            raise ValidationError("a notice needs a subject and a body")
        notice_id = next_reference(self.conn, "notice", NOTICE_PREFIX)
        insert(
            self.conn,
            "notice",
            {
                "id": notice_id,
                "kind": kind,
                "party_id": party_id,
                "subject": subject,
                "body": body,
                "channel": channel.value,
                "status": NoticeStatus.QUEUED.value,
                "reference": reference,
                "queued_at": now_text(),
            },
        )
        return self.get(notice_id)

    def get(self, notice_id: str) -> Notice:
        row = one(self.conn, "SELECT * FROM notice WHERE id = ?", (notice_id,))
        if row is None:
            raise NotFound("notice", notice_id)
        return Notice.from_row(row)

    def pending(self, limit: int = 100) -> list[Notice]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM notice WHERE status = 'queued' ORDER BY queued_at LIMIT ?",
            (limit,),
        )
        return [Notice.from_row(row) for row in rows]

    def for_party(self, party_id: str) -> list[Notice]:
        rows = all_rows(
            self.conn, "SELECT * FROM notice WHERE party_id = ? ORDER BY id", (party_id,)
        )
        return [Notice.from_row(row) for row in rows]

    def mark_sent(self, notice_id: str) -> Notice:
        update(
            self.conn,
            "notice",
            {"id": notice_id},
            {"status": NoticeStatus.SENT.value, "sent_at": now_text()},
        )
        return self.get(notice_id)

    def mark_failed(self, notice_id: str, error: str) -> Notice:
        notice = self.get(notice_id)
        update(
            self.conn,
            "notice",
            {"id": notice_id},
            {
                "status": NoticeStatus.FAILED.value,
                "attempts": notice.attempts + 1,
                "last_error": error,
            },
        )
        return self.get(notice_id)

    def retry(self, notice_id: str) -> Notice:
        notice = self.get(notice_id)
        if notice.status is not NoticeStatus.FAILED:
            raise ValidationError(f"{notice_id} is {notice.status.value}, not failed")
        update(self.conn, "notice", {"id": notice_id}, {"status": NoticeStatus.QUEUED.value})
        return self.get(notice_id)

    def cancel(self, notice_id: str) -> Notice:
        update(
            self.conn,
            "notice",
            {"id": notice_id},
            {"status": NoticeStatus.CANCELLED.value},
        )
        return self.get(notice_id)

    def stuck(self) -> list[Notice]:
        """Failed too many times for the sender to keep trying."""
        rows = all_rows(
            self.conn,
            "SELECT * FROM notice WHERE status = 'failed' AND attempts >= ? ORDER BY id",
            (MAX_ATTEMPTS,),
        )
        return [Notice.from_row(row) for row in rows]

    def counts(self) -> dict[NoticeStatus, int]:
        rows = all_rows(self.conn, "SELECT status, COUNT(*) AS n FROM notice GROUP BY status")
        return {NoticeStatus(row["status"]): row["n"] for row in rows}
