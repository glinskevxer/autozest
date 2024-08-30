"""The arbitration desk's work list, aged.

A claim that has been open eleven days is a different problem from one opened
this morning, and the desk cannot see that from a list sorted by reference. So
this ages them, and it ages them against the last thing that happened rather
than the day they were opened. A claim chased twice this week is being worked;
one opened last Tuesday and untouched since is not, and those two look
identical on any report that only knows the open date.

Escalation is the same idea with a threshold on it. Over the limit a claim goes
on the manager's list whether or not anybody has looked at it, because at that
point the answer to why it is still open is somebody else's problem.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from autozest.arbitration.claims import Claim, ClaimRepository, ClaimStatus
from autozest.block.sales import SaleRepository
from autozest.money import Money, total
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar

#: Business days untouched before a claim goes on the manager's list.
ESCALATION_DAYS = 5


@dataclass(frozen=True)
class AgedClaim:
    """One open claim and how stale it is."""

    claim: Claim
    run_number: str
    open_days: int
    idle_days: int
    last_actor: str | None
    last_action: str | None

    @property
    def needs_escalating(self) -> bool:
        return self.idle_days >= ESCALATION_DAYS

    @property
    def exposure(self) -> Money:
        return self.claim.claimed or Money.zero()

    def line(self) -> str:
        tail = f"{self.last_action} by {self.last_actor}" if self.last_action else "nothing yet"
        return (
            f"{self.claim.id}  {self.run_number}  {self.claim.defect.value}  "
            f"open {self.open_days}d idle {self.idle_days}d  {tail}"
        )


@dataclass(frozen=True)
class ClaimQueue:
    """Everything open, aged."""

    as_at: datetime
    claims: tuple[AgedClaim, ...]

    @property
    def count(self) -> int:
        return len(self.claims)

    @property
    def escalations(self) -> tuple[AgedClaim, ...]:
        return tuple(aged for aged in self.claims if aged.needs_escalating)

    @property
    def exposure(self) -> Money:
        return total([aged.exposure for aged in self.claims])

    def oldest(self) -> AgedClaim | None:
        if not self.claims:
            return None
        return max(self.claims, key=lambda aged: aged.open_days)

    def by_status(self) -> dict[ClaimStatus, int]:
        counts: dict[ClaimStatus, int] = {}
        for aged in self.claims:
            counts[aged.claim.status] = counts.get(aged.claim.status, 0) + 1
        return counts


def build(
    conn: sqlite3.Connection,
    *,
    as_at: datetime | None = None,
    calendar: BusinessCalendar | None = None,
) -> ClaimQueue:
    """Age every open claim."""
    moment = as_at or datetime.now().astimezone()
    business = calendar or DEFAULT_CALENDAR
    repository = ClaimRepository(conn)
    sales = SaleRepository(conn)

    aged: list[AgedClaim] = []
    for claim in repository.open_claims():
        events = repository.events(claim.id)
        last = events[-1] if events else None
        opened = claim.opened_at or moment
        touched = last.made_at if last and last.made_at else opened
        aged.append(
            AgedClaim(
                claim=claim,
                run_number=sales.run(claim.run_id).number,
                open_days=_days(business, opened, moment),
                idle_days=_days(business, touched, moment),
                last_actor=last.actor if last else None,
                last_action=last.kind if last else None,
            )
        )
    aged.sort(key=lambda item: (-item.idle_days, item.claim.id))
    return ClaimQueue(as_at=moment, claims=tuple(aged))


def _days(calendar: BusinessCalendar, then: datetime, now: datetime) -> int:
    if now < then:
        return 0
    return calendar.business_days_between(then.date(), now.date())


def render(conn: sqlite3.Connection, as_at: datetime | None = None) -> list[str]:
    """The queue as printable lines, staleest first."""
    queue = build(conn, as_at=as_at)
    lines = [f"arbitration queue as at {queue.as_at:%Y-%m-%d %H:%M}"]
    lines.extend(f"  {aged.line()}" for aged in queue.claims)
    lines.append(f"{queue.count} open, {len(queue.escalations)} for escalation")
    lines.append(f"exposure claimed {queue.exposure}")
    for status, count in sorted(queue.by_status().items(), key=lambda pair: pair[0].value):
        lines.append(f"  {status.value:<14} {count}")
    return lines
