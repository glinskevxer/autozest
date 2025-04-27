"""How each party wants to be told things.

One preference per party per kind of message, because the answers really are
different. A dealer wants an invoice by email and an arbitration decision by
phone, and sending both the same way is how a decision gets read four days
late, by which time the appeal window has closed.

There is a default and it is post. That looks old fashioned and it is the only
channel that cannot silently fail: an email to an address nobody reads looks
exactly like an email that was read, and this system has no way to tell.

A preference has a verified date. An address nobody has confirmed is an address
somebody typed, and the difference matters for the letters that start a clock.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from autozest.errors import NotFound, ValidationError
from autozest.notices import Channel
from autozest.storage.records import all_rows, insert, one, update

#: Used when a party has said nothing. Post cannot silently fail.
DEFAULT_CHANNEL = Channel.POST

#: Message kinds that start a clock and therefore want a verified address.
CLOCK_STARTING = frozenset({"claim.decided", "payment.due"})


@dataclass(frozen=True)
class Preference:
    """How one party wants one kind of message."""

    party_id: str
    kind: str
    channel: Channel
    address: str
    verified_on: date | None = None

    @property
    def is_verified(self) -> bool:
        return self.verified_on is not None

    def suits(self, kind: str) -> bool:
        """Whether this preference can carry a message of this kind."""
        if kind not in CLOCK_STARTING:
            return True
        return self.is_verified or self.channel is Channel.POST

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Preference:
        return cls(
            party_id=row["party_id"],
            kind=row["kind"],
            channel=Channel(row["channel"]),
            address=row["address"],
            verified_on=(
                date.fromisoformat(row["verified_on"]) if row["verified_on"] else None
            ),
        )


@dataclass(frozen=True)
class Routing:
    """Where one message is going, and why."""

    channel: Channel
    address: str | None
    reason: str

    @property
    def is_default(self) -> bool:
        return self.address is None


class ContactBook:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def set_preference(
        self,
        party_id: str,
        kind: str,
        channel: Channel,
        address: str,
        *,
        verified_on: date | None = None,
    ) -> Preference:
        if not address.strip():
            raise ValidationError("a contact preference needs an address")
        values = {
            "channel": channel.value,
            "address": address,
            "verified_on": verified_on.isoformat() if verified_on else None,
        }
        if self.find(party_id, kind) is not None:
            update(
                self.conn, "contact_preference", {"party_id": party_id, "kind": kind}, values
            )
        else:
            insert(
                self.conn,
                "contact_preference",
                {"party_id": party_id, "kind": kind, **values},
            )
        return self.get(party_id, kind)

    def get(self, party_id: str, kind: str) -> Preference:
        found = self.find(party_id, kind)
        if found is None:
            raise NotFound("contact preference", f"{party_id}/{kind}")
        return found

    def find(self, party_id: str, kind: str) -> Preference | None:
        row = one(
            self.conn,
            "SELECT * FROM contact_preference WHERE party_id = ? AND kind = ?",
            (party_id, kind),
        )
        return Preference.from_row(row) if row else None

    def for_party(self, party_id: str) -> list[Preference]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM contact_preference WHERE party_id = ? ORDER BY kind",
            (party_id,),
        )
        return [Preference.from_row(row) for row in rows]

    def verify(self, party_id: str, kind: str, *, on: date | None = None) -> Preference:
        self.get(party_id, kind)
        update(
            self.conn,
            "contact_preference",
            {"party_id": party_id, "kind": kind},
            {"verified_on": (on or date.today()).isoformat()},
        )
        return self.get(party_id, kind)

    def route(self, party_id: str, kind: str) -> Routing:
        """Where a message of this kind goes, and why it goes there."""
        preference = self.find(party_id, kind)
        if preference is None:
            return Routing(DEFAULT_CHANNEL, None, "nothing on file")
        if not preference.suits(kind):
            return Routing(
                DEFAULT_CHANNEL,
                None,
                f"{preference.channel.value} address is not verified and this starts a clock",
            )
        return Routing(preference.channel, preference.address, "as asked for")

    def unverified(self) -> list[Preference]:
        """Preferences that would fall back for a clock starting letter."""
        return [
            preference
            for row in all_rows(
                self.conn, "SELECT * FROM contact_preference WHERE verified_on IS NULL"
            )
            for preference in [Preference.from_row(row)]
            if preference.channel is not Channel.POST
        ]
