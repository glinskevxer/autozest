"""What a particular seller wants looked at.

A fleet's contract says every unit gets a tyre depth and an underbody check. A
finance company's repossessions want the interior graded, because on a
repossession that is where the money is. An inspector holding a phone in the
rain cannot be expected to remember which is which.

So a template is a list of prompts in the order somebody walks around a car,
and the required ones are checked before a report can be signed. That check is
the only teeth this has: a template nobody enforces is a list on a wall.

There is a house template with no seller on it, used when a seller has none of
their own. Everything gets looked at the same way unless somebody has asked for
more.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from autozest.errors import NotFound, PolicyViolation, ValidationError
from autozest.inspection.damage import Panel
from autozest.storage.records import all_rows, insert, now_text, one, update


@dataclass(frozen=True)
class Prompt:
    """One thing to look at."""

    ordinal: int
    prompt: str
    panel: Panel | None = None
    required: bool = True

    def __str__(self) -> str:
        mark = "*" if self.required else " "
        where = f" [{self.panel.value}]" if self.panel else ""
        return f"{mark}{self.ordinal:>3}. {self.prompt}{where}"


@dataclass(frozen=True)
class Template:
    """One seller's list, in walking order."""

    id: str
    name: str
    seller_id: str | None = None
    active: bool = True
    prompts: tuple[Prompt, ...] = ()

    @property
    def is_house(self) -> bool:
        return self.seller_id is None

    @property
    def required(self) -> tuple[Prompt, ...]:
        return tuple(prompt for prompt in self.prompts if prompt.required)

    def describe(self) -> str:
        whose = "house" if self.is_house else self.seller_id
        return (
            f"{self.name} ({whose}): {len(self.prompts)} checks, {len(self.required)} required"
        )

    @classmethod
    def from_row(cls, row: dict[str, Any], prompts: tuple[Prompt, ...]) -> Template:
        return cls(
            id=row["id"],
            name=row["name"],
            seller_id=row["seller_id"],
            active=bool(row["active"]),
            prompts=prompts,
        )


class TemplateBook:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        template_id: str,
        name: str,
        prompts: list[Prompt],
        *,
        seller_id: str | None = None,
    ) -> Template:
        if not prompts:
            raise ValidationError("a template with no checks asks for nothing")
        insert(
            self.conn,
            "inspection_template",
            {
                "id": template_id,
                "seller_id": seller_id,
                "name": name,
                "active": 1,
                "created_at": now_text(),
            },
        )
        for prompt in sorted(prompts, key=lambda item: item.ordinal):
            insert(
                self.conn,
                "template_check",
                {
                    "template_id": template_id,
                    "ordinal": prompt.ordinal,
                    "prompt": prompt.prompt,
                    "panel": prompt.panel.value if prompt.panel else None,
                    "required": int(prompt.required),
                },
            )
        return self.get(template_id)

    def get(self, template_id: str) -> Template:
        row = one(self.conn, "SELECT * FROM inspection_template WHERE id = ?", (template_id,))
        if row is None:
            raise NotFound("inspection template", template_id)
        return Template.from_row(row, self._prompts(template_id))

    def _prompts(self, template_id: str) -> tuple[Prompt, ...]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM template_check WHERE template_id = ? ORDER BY ordinal",
            (template_id,),
        )
        return tuple(
            Prompt(
                ordinal=row["ordinal"],
                prompt=row["prompt"],
                panel=Panel(row["panel"]) if row["panel"] else None,
                required=bool(row["required"]),
            )
            for row in rows
        )

    def for_seller(self, seller_id: str) -> Template | None:
        """The seller's own template, or the house one, or nothing."""
        row = one(
            self.conn,
            "SELECT * FROM inspection_template WHERE seller_id = ? AND active = 1 LIMIT 1",
            (seller_id,),
        )
        if row is not None:
            return Template.from_row(row, self._prompts(row["id"]))
        return self.house()

    def house(self) -> Template | None:
        row = one(
            self.conn,
            "SELECT * FROM inspection_template WHERE seller_id IS NULL AND active = 1 LIMIT 1",
        )
        return Template.from_row(row, self._prompts(row["id"])) if row else None

    def retire(self, template_id: str) -> Template:
        self.get(template_id)
        update(self.conn, "inspection_template", {"id": template_id}, {"active": 0})
        return self.get(template_id)

    def templates(self) -> list[Template]:
        rows = all_rows(
            self.conn,
            "SELECT * FROM inspection_template WHERE active = 1 ORDER BY seller_id, name",
        )
        return [Template.from_row(row, self._prompts(row["id"])) for row in rows]

    def require_covered(self, seller_id: str, answered: set[int]) -> None:
        """Raise unless every required prompt has been answered.

        This is the only teeth a template has. Without it a template is a list
        on a wall that nobody looks at twice.
        """
        template = self.for_seller(seller_id)
        if template is None:
            return
        missing = [prompt for prompt in template.required if prompt.ordinal not in answered]
        if missing:
            raise PolicyViolation(
                "inspection.template",
                f"{template.name} still wants: "
                + "; ".join(prompt.prompt for prompt in missing),
                seller=seller_id,
                missing=len(missing),
            )
