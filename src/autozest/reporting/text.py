"""Fixed width text output.

Everything this system prints ends up in one of three places: a terminal, a
dot matrix printer in the block office, or an email to a seller who will read
it on a phone. That rules out anything clever. Columns are padded with spaces,
nothing is coloured, and the width is fixed so a run sheet printed on Tuesday
lines up with one printed on Thursday.

Money is right aligned and text is left aligned, always, because the eye scans
a column of figures down the right hand edge and a misaligned total is the one
thing nobody forgives.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from autozest.errors import ValidationError
from autozest.money import Money

DEFAULT_WIDTH = 96
ELLIPSIS = ".."


class Align(Enum):
    LEFT = "left"
    RIGHT = "right"
    CENTRE = "centre"


@dataclass(frozen=True)
class Column:
    """One column of a table."""

    heading: str
    width: int
    align: Align = Align.LEFT

    def __post_init__(self) -> None:
        if self.width < 1:
            raise ValidationError(f"a column cannot be {self.width} wide")

    def render(self, value: object) -> str:
        text = format_value(value)
        if len(text) > self.width:
            text = truncate(text, self.width)
        if self.align is Align.RIGHT:
            return text.rjust(self.width)
        if self.align is Align.CENTRE:
            return text.center(self.width)
        return text.ljust(self.width)


def format_value(value: object) -> str:
    """One place that decides how each kind of thing looks."""
    if value is None:
        return ""
    if isinstance(value, Money):
        return str(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def truncate(text: str, width: int) -> str:
    """Cut text to width, marking that something was cut."""
    if width <= 0:
        raise ValidationError("cannot truncate to nothing")
    if len(text) <= width:
        return text
    if width <= len(ELLIPSIS):
        return text[:width]
    return text[: width - len(ELLIPSIS)] + ELLIPSIS


def money_column(heading: str, width: int = 12) -> Column:
    return Column(heading, width, Align.RIGHT)


@dataclass
class Table:
    """A table of rows, rendered one line at a time."""

    columns: Sequence[Column]
    gap: str = "  "

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValidationError("a table needs at least one column")

    @property
    def width(self) -> int:
        return sum(c.width for c in self.columns) + len(self.gap) * (len(self.columns) - 1)

    def heading(self) -> str:
        return self.gap.join(c.render(c.heading) for c in self.columns)

    def rule(self, character: str = "-") -> str:
        return character * self.width

    def row(self, values: Sequence[object]) -> str:
        if len(values) != len(self.columns):
            raise ValidationError(
                f"the table has {len(self.columns)} columns and the row has {len(values)}"
            )
        return self.gap.join(c.render(v) for c, v in zip(self.columns, values, strict=True))

    def render(self, rows: Iterable[Sequence[object]], *, ruled: bool = True) -> list[str]:
        lines = [self.heading()]
        if ruled:
            lines.append(self.rule())
        lines.extend(self.row(row) for row in rows)
        return lines


def key_values(pairs: Sequence[tuple[str, object]], *, width: int | None = None) -> list[str]:
    """A block of label and value lines, labels padded to line up."""
    if not pairs:
        return []
    label_width = width or max(len(label) for label, _ in pairs)
    return [f"{label.ljust(label_width)}  {format_value(value)}" for label, value in pairs]


def heading(title: str, *, width: int = DEFAULT_WIDTH, character: str = "=") -> list[str]:
    """A title with a rule under it."""
    return [title, character * min(width, max(len(title), 1))]


def wrap(text: str, width: int = DEFAULT_WIDTH) -> list[str]:
    """Break text onto lines without splitting a word that fits on its own."""
    if width < 1:
        raise ValidationError("cannot wrap to nothing")
    lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def indent(lines: Iterable[str], by: int = 4) -> list[str]:
    prefix = " " * by
    return [f"{prefix}{line}" if line else line for line in lines]
