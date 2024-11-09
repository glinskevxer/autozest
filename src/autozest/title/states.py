"""What each state does with a title.

Fifty sets of rules and no two the same. Only three of the differences change
what this building does, so only three are recorded: how long a duplicate
takes, whether the state recognises a rebuilt brand at all, and whether it
wants the unit physically inspected before it will retitle.

The one that catches people is recognition. A rebuilt title from a state that
issues them is not necessarily a title the buyer's state will accept, and a
unit sold across that border is a unit the buyer cannot register. That is not
an arbitration matter and it is not our fault, and it is still worth warning
about at the point of sale rather than six weeks later.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from autozest.errors import NotFound, ValidationError
from autozest.storage.records import all_rows, insert, one, update
from autozest.timing import DEFAULT_CALENDAR, BusinessCalendar
from autozest.title.branding import TitleBrand


@dataclass(frozen=True)
class StateRule:
    """One state's handling."""

    state: str
    duplicate_days: int
    transfer_days: int
    recognises_rebuilt: bool = True
    needs_inspection: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        if len(self.state) != 2 or not self.state.isalpha():
            raise ValidationError(f"{self.state!r} is not a two letter state")
        if self.duplicate_days < 0 or self.transfer_days < 0:
            raise ValidationError("a turnaround cannot be negative")

    def accepts(self, brand: TitleBrand) -> bool:
        """Whether a unit on this brand can be titled here at all."""
        if brand is TitleBrand.JUNK:
            return False
        if brand is TitleBrand.REBUILT:
            return self.recognises_rebuilt
        return True

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> StateRule:
        return cls(
            state=row["state"],
            duplicate_days=row["duplicate_days"],
            transfer_days=row["transfer_days"],
            recognises_rebuilt=bool(row["recognises_rebuilt"]),
            needs_inspection=bool(row["needs_inspection"]),
            note=row["note"],
        )


@dataclass(frozen=True)
class CrossBorderWarning:
    """Something to say at the block about where a unit is going."""

    code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


class TitleStates:
    def __init__(
        self, conn: sqlite3.Connection, calendar: BusinessCalendar | None = None
    ) -> None:
        self.conn = conn
        self.calendar = calendar or DEFAULT_CALENDAR

    def set_rule(self, rule: StateRule) -> StateRule:
        values = {
            "duplicate_days": rule.duplicate_days,
            "transfer_days": rule.transfer_days,
            "recognises_rebuilt": int(rule.recognises_rebuilt),
            "needs_inspection": int(rule.needs_inspection),
            "note": rule.note,
        }
        if one(self.conn, "SELECT state FROM title_state_rule WHERE state = ?", (rule.state,)):
            update(self.conn, "title_state_rule", {"state": rule.state}, values)
        else:
            insert(self.conn, "title_state_rule", {"state": rule.state, **values})
        return self.rule(rule.state)

    def rule(self, state: str) -> StateRule:
        row = one(self.conn, "SELECT * FROM title_state_rule WHERE state = ?", (state.upper(),))
        if row is None:
            raise NotFound("title state rule", state.upper())
        return StateRule.from_row(row)

    def rules(self) -> list[StateRule]:
        rows = all_rows(self.conn, "SELECT * FROM title_state_rule ORDER BY state")
        return [StateRule.from_row(row) for row in rows]

    def duplicate_due(self, state: str, applied_on: date) -> date:
        """When a duplicate application should be back."""
        return self.calendar.add_business_days(applied_on, self.rule(state).duplicate_days)

    def transfer_due(self, state: str, sold_on: date) -> date:
        """When the buyer's state expects the transfer done."""
        return self.calendar.add_business_days(sold_on, self.rule(state).transfer_days)

    def cross_border(
        self, issuing_state: str, destination_state: str, brand: TitleBrand
    ) -> list[CrossBorderWarning]:
        """What to say at the block about a unit crossing a border.

        Not an arbitration matter and not our fault, and still worth saying
        before the hammer rather than six weeks later.
        """
        warnings: list[CrossBorderWarning] = []
        if issuing_state.upper() == destination_state.upper():
            return warnings
        try:
            destination = self.rule(destination_state)
        except NotFound:
            return [
                CrossBorderWarning(
                    "unknown_state",
                    f"we have no rules on file for {destination_state.upper()}",
                )
            ]
        if not destination.accepts(brand):
            warnings.append(
                CrossBorderWarning(
                    "brand_not_accepted",
                    f"{destination.state} will not title a {brand.value} unit",
                )
            )
        if destination.needs_inspection:
            warnings.append(
                CrossBorderWarning(
                    "inspection_needed",
                    f"{destination.state} wants the unit inspected before it retitles",
                )
            )
        if destination.transfer_days <= 5:
            warnings.append(
                CrossBorderWarning(
                    "tight_transfer",
                    f"{destination.state} expects the transfer inside "
                    f"{destination.transfer_days} business days",
                )
            )
        return warnings

    def slowest(self, limit: int = 5) -> list[StateRule]:
        """The states that hold a duplicate longest, for the chase list."""
        return sorted(self.rules(), key=lambda rule: -rule.duplicate_days)[:limit]
