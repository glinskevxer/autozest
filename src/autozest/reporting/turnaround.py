"""How long things take.

Four numbers, and the reason they are together in one module is that they are
the four the dealer council asks about every quarter. Nobody has ever asked how
many claims were opened; they ask how long one takes to answer, how long a
title takes to arrive, and how much of that is us.

Medians rather than means, throughout. One title stuck behind a Michigan
duplicate for eleven weeks drags a mean far enough to make the number useless,
and the question being asked is what a normal one looks like.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from autozest.arbitration.claims import ClaimRepository
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar
from autozest.title.service import TitleService, TitleStatus


@dataclass(frozen=True)
class Spread:
    """A set of durations, described the way a person would describe it."""

    name: str
    samples: tuple[int, ...]

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def median(self) -> float:
        return float(median(self.samples)) if self.samples else 0.0

    @property
    def worst(self) -> int:
        return max(self.samples, default=0)

    @property
    def best(self) -> int:
        return min(self.samples, default=0)

    def over(self, threshold: int) -> int:
        """How many took longer than a target."""
        return sum(1 for sample in self.samples if sample > threshold)

    def __str__(self) -> str:
        if not self.samples:
            return f"{self.name}: nothing to measure"
        return (
            f"{self.name}: {self.count} measured, median {self.median:g} days, "
            f"best {self.best}, worst {self.worst}"
        )


def _days(calendar: BusinessCalendar, start: datetime, end: datetime) -> int:
    if end < start:
        return 0
    return calendar.business_days_between(start.date(), end.date())


def claim_turnaround(
    conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
) -> Spread:
    """From a claim being opened to it being decided."""
    business = calendar or DEFAULT_CALENDAR
    claims = ClaimRepository(conn)
    samples: list[int] = []
    for row in conn.execute("SELECT id FROM arbitration_claim ORDER BY id").fetchall():
        claim = claims.get(row["id"])
        if not claim.status.counts_as_decided:
            continue
        if claim.opened_at is None or claim.decided_at is None:
            continue
        samples.append(_days(business, claim.opened_at, claim.decided_at))
    return Spread("claim turnaround", tuple(samples))


def claim_first_touch(
    conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
) -> Spread:
    """From a claim arriving to somebody picking it up.

    The half of the turnaround the desk actually controls.
    """
    business = calendar or DEFAULT_CALENDAR
    claims = ClaimRepository(conn)
    samples: list[int] = []
    for row in conn.execute("SELECT id FROM arbitration_claim ORDER BY id").fetchall():
        claim = claims.get(row["id"])
        events = [event for event in claims.events(claim.id) if event.kind != "opened"]
        if claim.opened_at is None or not events or events[0].made_at is None:
            continue
        samples.append(_days(business, claim.opened_at, events[0].made_at))
    return Spread("claim first touch", tuple(samples))


def title_arrival(conn: sqlite3.Connection, calendar: BusinessCalendar | None = None) -> Spread:
    """From a title being expected to it turning up."""
    business = calendar or DEFAULT_CALENDAR
    titles = TitleService(conn)
    samples: list[int] = []
    rows = conn.execute(
        "SELECT consignment_id FROM title_record WHERE received_on IS NOT NULL"
    ).fetchall()
    for row in rows:
        record = titles.get(row["consignment_id"])
        movements = titles.movements(record.consignment_id)
        opened = next(
            (m.made_at for m in movements if m.to_status is TitleStatus.EXPECTED), None
        )
        arrived = next(
            (m.made_at for m in movements if m.to_status is TitleStatus.ON_HAND), None
        )
        if opened is None or arrived is None:
            continue
        samples.append(_days(business, opened, arrived))
    return Spread("title arrival", tuple(samples))


def title_despatch(
    conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
) -> Spread:
    """From a title arriving to it going out to the buyer.

    This one is entirely ours. A title sitting in a drawer for nine days is not
    the state's fault and not the seller's.
    """
    business = calendar or DEFAULT_CALENDAR
    titles = TitleService(conn)
    samples: list[int] = []
    rows = conn.execute(
        "SELECT consignment_id FROM title_record WHERE status = 'sent'"
    ).fetchall()
    for row in rows:
        movements = titles.movements(row["consignment_id"])
        arrived = next(
            (m.made_at for m in movements if m.to_status is TitleStatus.ON_HAND), None
        )
        sent = next((m.made_at for m in movements if m.to_status is TitleStatus.SENT), None)
        if arrived is None or sent is None:
            continue
        samples.append(_days(business, arrived, sent))
    return Spread("title despatch", tuple(samples))


def everything(
    conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
) -> list[Spread]:
    """The four numbers the council asks about."""
    return [
        claim_first_touch(conn, calendar),
        claim_turnaround(conn, calendar),
        title_arrival(conn, calendar),
        title_despatch(conn, calendar),
    ]


def render(conn: sqlite3.Connection, calendar: BusinessCalendar | None = None) -> list[str]:
    lines = ["turnaround, in business days"]
    for spread in everything(conn, calendar):
        lines.append(f"  {spread}")
    open_claims = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM arbitration_claim "
            "WHERE status IN ('open', 'under_review')"
        ).fetchone()["n"]
    )
    waiting = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM title_record WHERE status IN "
            "('expected', 'absent', 'attorney', 'lost_applied')"
        ).fetchone()["n"]
    )
    lines.append(f"{open_claims} claims open, {waiting} titles outstanding")
    return lines
