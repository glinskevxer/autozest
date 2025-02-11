"""Sale templates.

The same three sales run every week with the same lanes. Building each by hand
is four minutes and one forgotten lane a quarter, and a forgotten lane is only
noticed when forty units have nowhere to go.

A template names a weekday rather than a date, because that is how the calendar
is actually kept: the Tuesday dealer sale, the Thursday fleet sale. Asking for
the next one from a date is then a question the template can answer.

Nothing is generated automatically. A sale appearing on its own because a
template existed is a sale nobody decided to hold, and cancelling one of those
is harder than creating the one you wanted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from autozest.block.models import Sale
from autozest.block.sales import SaleRepository
from autozest.errors import NotFound, PolicyViolation, ValidationError
from autozest.storage.records import all_rows, insert, now_text, one, update

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class Template:
    """A sale that runs on the same day every week."""

    id: str
    name: str
    weekday: int
    title: str
    active: bool = True
    lanes: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValidationError(f"{self.weekday} is not a weekday")
        if not self.title.strip():
            raise ValidationError("a template needs a title")

    @property
    def day_name(self) -> str:
        return WEEKDAYS[self.weekday]

    @property
    def capacity(self) -> int:
        return sum(capacity for _, capacity in self.lanes)

    def next_after(self, day: date) -> date:
        """The next occurrence strictly after a date."""
        ahead = (self.weekday - day.weekday()) % 7
        return day + timedelta(days=ahead or 7)

    def describe(self) -> str:
        letters = ", ".join(letter for letter, _ in self.lanes) or "no lanes"
        return f"{self.name}: every {self.day_name}, {letters}, {self.capacity} slots"

    @classmethod
    def from_row(cls, row: dict[str, Any], lanes: tuple[tuple[str, int], ...]) -> Template:
        return cls(
            id=row["id"],
            name=row["name"],
            weekday=row["weekday"],
            title=row["title"],
            active=bool(row["active"]),
            lanes=lanes,
        )


class TemplateService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.sales = SaleRepository(conn)

    def create(
        self,
        template_id: str,
        name: str,
        weekday: int,
        title: str,
        lanes: dict[str, int],
    ) -> Template:
        if not lanes:
            raise ValidationError("a template with no lanes builds nothing")
        template = Template(
            id=template_id,
            name=name,
            weekday=weekday,
            title=title,
            lanes=tuple(sorted((letter.upper(), size) for letter, size in lanes.items())),
        )
        insert(
            self.conn,
            "sale_template",
            {
                "id": template.id,
                "name": template.name,
                "weekday": template.weekday,
                "title": template.title,
                "active": int(template.active),
                "created_at": now_text(),
            },
        )
        for letter, capacity in template.lanes:
            insert(
                self.conn,
                "template_lane",
                {"template_id": template.id, "letter": letter, "capacity": capacity},
            )
        return self.get(template.id)

    def get(self, template_id: str) -> Template:
        row = one(self.conn, "SELECT * FROM sale_template WHERE id = ?", (template_id,))
        if row is None:
            raise NotFound("sale template", template_id)
        return Template.from_row(row, self._lanes(template_id))

    def _lanes(self, template_id: str) -> tuple[tuple[str, int], ...]:
        rows = all_rows(
            self.conn,
            "SELECT letter, capacity FROM template_lane WHERE template_id = ? ORDER BY letter",
            (template_id,),
        )
        return tuple((row["letter"], row["capacity"]) for row in rows)

    def templates(self, *, active_only: bool = True) -> list[Template]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM sale_template WHERE (? = 0 OR active = 1) ORDER BY weekday, name",
            (int(active_only),),
        )
        return [Template.from_row(row, self._lanes(row["id"])) for row in rows]

    def retire(self, template_id: str) -> Template:
        self.get(template_id)
        update(self.conn, "sale_template", {"id": template_id}, {"active": 0})
        return self.get(template_id)

    def build(self, template_id: str, on: date) -> Sale:
        """Create one sale from a template, on a date somebody chose.

        The date is checked against the template's weekday. Building Tuesday's
        sale on a Thursday is always a typo and never an intention.
        """
        template = self.get(template_id)
        if not template.active:
            raise PolicyViolation(
                "template.retired", "that template is retired", template=template_id
            )
        if on.weekday() != template.weekday:
            raise PolicyViolation(
                "template.wrong_day",
                f"{template.name} runs on a {template.day_name}, "
                f"{on.isoformat()} is a {WEEKDAYS[on.weekday()]}",
                template=template_id,
            )
        return self.sales.create(on, template.title, dict(template.lanes))

    def build_next(self, template_id: str, *, after: date | None = None) -> Sale:
        """Build the next one, which is what anybody actually wants."""
        template = self.get(template_id)
        return self.build(template_id, template.next_after(after or date.today()))

    def upcoming(self, *, after: date | None = None) -> list[tuple[Template, date]]:
        """Every active template and when it next runs."""
        day = after or date.today()
        return [(template, template.next_after(day)) for template in self.templates()]
