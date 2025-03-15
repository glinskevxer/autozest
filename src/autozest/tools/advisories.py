"""Which security advisories this project has looked at and accepted.

A dependency audit that reports something nobody has decided about is a build
that goes red and stays red until somebody turns the job off. So an advisory is
either fixed by moving the pin or it is accepted here, by identifier, with the
reason and the date somebody looked.

An acceptance expires. That is the whole point: an advisory accepted in March
because the fix was not out yet has to be looked at again in June, and an
acceptance with no end date is a decision nobody ever revisits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Accepted:
    """One advisory somebody decided to live with, for now."""

    identifier: str
    package: str
    reason: str
    accepted_on: date
    review_by: date

    def is_current(self, on: date) -> bool:
        return self.accepted_on <= on <= self.review_by

    def is_overdue(self, on: date) -> bool:
        return on > self.review_by

    def __str__(self) -> str:
        return (
            f"{self.identifier} ({self.package}) accepted {self.accepted_on}, "
            f"review by {self.review_by}: {self.reason}"
        )


#: Nothing is accepted at the moment. Every pin is on a version with no known
#: advisory against it, which is the state to keep this in.
ACCEPTED: tuple[Accepted, ...] = ()


def by_identifier() -> dict[str, Accepted]:
    return {item.identifier: item for item in ACCEPTED}


def is_accepted(identifier: str, *, on: date | None = None) -> bool:
    """Whether this advisory has been looked at and is still accepted."""
    entry = by_identifier().get(identifier)
    return entry is not None and entry.is_current(on or date.today())


def overdue(on: date | None = None) -> list[Accepted]:
    """Acceptances that have run past their review date."""
    today = on or date.today()
    return [item for item in ACCEPTED if item.is_overdue(today)]


def unaccepted(found: list[str], *, on: date | None = None) -> list[str]:
    """Advisories an audit reported that nobody has decided about."""
    return sorted(item for item in found if not is_accepted(item, on=on))


def render(on: date | None = None) -> list[str]:
    """What the acceptance list says today."""
    today = on or date.today()
    if not ACCEPTED:
        return ["no advisories accepted, every pin is clean"]
    lines = [str(item) for item in ACCEPTED]
    stale = overdue(today)
    lines.append(f"{len(ACCEPTED)} accepted, {len(stale)} past their review date")
    return lines
