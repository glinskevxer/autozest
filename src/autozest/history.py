"""What changed in each version, kept where the code can check it.

A changelog in a file drifts. Nobody notices, because nothing reads it. This
one is a list the tests read, so a version without notes fails the build and a
note about a version that does not exist fails it too.

The categories are the ones that matter to somebody deciding whether to
upgrade: what is new, what changed under them, and what will break if they do
not read it first. Everything else is noise on a page somebody is scanning for
the third thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from autozest import __version__


@dataclass(frozen=True)
class Release:
    """One version and what it did."""

    version: str
    released_on: date | None
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    breaking: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()

    @property
    def is_released(self) -> bool:
        return self.released_on is not None

    @property
    def has_breaking_changes(self) -> bool:
        return bool(self.breaking)

    @property
    def entries(self) -> int:
        return len(self.added) + len(self.changed) + len(self.breaking) + len(self.fixed)

    def render(self) -> list[str]:
        when = self.released_on.isoformat() if self.released_on else "unreleased"
        lines = [f"{self.version}  {when}"]
        for label, items in (
            ("breaking", self.breaking),
            ("added", self.added),
            ("changed", self.changed),
            ("fixed", self.fixed),
        ):
            for item in items:
                lines.append(f"  {label:<9} {item}")
        return lines


#: Newest first, which is how a changelog is read and how breaking_since walks.
HISTORY: tuple[Release, ...] = (
    Release(
        version="0.1.0",
        released_on=None,
        added=(
            "consignment, intake and the gate",
            "condition reports, damage grading and the announcement set",
            "the block: lanes, run order, lights, bidding and if bids",
            "arbitration with windows, thresholds, resolutions and one appeal tier",
            "titles, brands, holds and per state rules",
            "settlement: fees, a double entry ledger, payments, proceeds and periods",
            "floor plan credit, purchase limits and dealer groups",
            "reporting, csv exports and a reference card printed from the rules",
            "the az command line over all of it",
        ),
        changed=(),
        breaking=(),
        fixed=(),
    ),
)

BY_VERSION = {release.version: release for release in HISTORY}


def current() -> Release:
    """The release this build is."""
    return BY_VERSION[__version__]


def released() -> tuple[Release, ...]:
    return tuple(release for release in HISTORY if release.is_released)


def breaking_since(version: str) -> tuple[str, ...]:
    """Everything that breaks between a version and this one.

    What somebody upgrading needs and the only thing they will actually read.
    """
    found: list[str] = []
    for release in HISTORY:
        if release.version == version:
            break
        found.extend(release.breaking)
    return tuple(found)


def render() -> list[str]:
    lines: list[str] = []
    for release in HISTORY:
        lines.extend(release.render())
        lines.append("")
    return lines
