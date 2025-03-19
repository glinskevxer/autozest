"""The operator manual, written from the command line itself.

Every command's help text already says what it does, and the reference card
already prints the rules. What was missing was one document that puts them
together in the order somebody learns the building: what you do on Monday,
what happens on the night, what happens afterwards.

It is generated rather than written because a manual that is written is a
manual that is wrong within a quarter. This one cannot describe a command that
does not exist or miss one that does, and the test asserts exactly that.
"""

from __future__ import annotations

import click

from autozest.reporting import reference
from autozest.reporting.text import heading, wrap

WIDTH = 88

#: The order somebody learns the building, rather than alphabetical.
ORDER = (
    ("consign", "Booking units in and receiving them at the gate."),
    ("inspect", "Condition reports, and signing them off."),
    ("sale", "Building next Tuesday and deciding whether it can open."),
    ("block", "The ring, live, while units are crossing."),
    ("arb", "The arbitration desk."),
    ("title", "Paperwork in and paperwork out."),
    ("settle", "Posting, taking money in, paying sellers out."),
    ("yard", "Where the units physically are."),
    ("gate", "Keys, and letting a unit off the site."),
    ("roster", "Who is on which lane."),
    ("market", "What a unit is worth and what to ask."),
    ("limits", "Caps a buyer asked for or had imposed."),
    ("report", "Everything printed. Nothing here writes."),
    ("admin", "Backups, checks and the audit trail."),
)


def group_section(group: click.Group, name: str, blurb: str) -> list[str]:
    """One command group and everything under it."""
    lines = heading(f"az {name}", width=WIDTH, character="-")
    lines.extend(wrap(blurb, WIDTH))
    lines.append("")
    for command_name in sorted(group.commands):
        command = group.commands[command_name]
        summary = (command.help or "").strip().splitlines()
        first = summary[0] if summary else ""
        lines.append(f"  az {name} {command_name}")
        if first:
            lines.extend(f"      {line}" for line in wrap(first, WIDTH - 6))
    lines.append("")
    return lines


def render(cli: click.Group) -> list[str]:
    """The manual."""
    lines = heading("autozest", width=WIDTH)
    lines.extend(
        wrap(
            "Consignment, block and settlement for a wholesale vehicle auction. "
            "Every command below takes --db and --config, and every one of them "
            "exits non zero when it refuses something. The statuses are at the "
            "end of this document.",
            WIDTH,
        )
    )
    lines.append("")
    for name, blurb in ORDER:
        command = cli.commands.get(name)
        if isinstance(command, click.Group):
            lines.extend(group_section(command, name, blurb))
    lines.extend(reference.render())
    return lines


def documented_groups() -> set[str]:
    return {name for name, _ in ORDER}
