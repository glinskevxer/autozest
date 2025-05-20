"""``az roster``.

Used once a week by whoever builds the sheet, and once on a sale morning by
whoever is short a ring man. ``gaps`` is the command that matters: it answers
the only staffing question that stops a lane running.
"""

from __future__ import annotations

import click

from autozest.block.roster import Role, RosterService, Staff
from autozest.cli.context import Context
from autozest.reporting.text import Column, Table

pass_context = click.make_pass_decorator(Context)

ROLE_CHOICE = click.Choice([role.value for role in Role])


@click.group()
def roster() -> None:
    """Staff, and who is on which lane."""


@roster.command("add")
@click.argument("staff_id")
@click.argument("name")
@click.argument("role", type=ROLE_CHOICE)
@pass_context
def add(ctx: Context, staff_id: str, name: str, role: str) -> None:
    """Put somebody on the staff list."""
    person = RosterService(ctx.open()).add(Staff(id=staff_id, name=name, role=Role(role)))
    click.echo(f"{person.id}  {person.name}  {person.role.value}")


@roster.command("retire")
@click.argument("staff_id")
@pass_context
def retire(ctx: Context, staff_id: str) -> None:
    """Take somebody off the list without losing their history."""
    person = RosterService(ctx.open()).retire(staff_id)
    click.echo(f"{person.name} is off the roster")


@roster.command("people")
@click.option("--role", default=None, type=ROLE_CHOICE, help="Narrow to one role.")
@pass_context
def people(ctx: Context, role: str | None) -> None:
    """Who is on the staff list."""
    service = RosterService(ctx.open())
    table = Table(
        [Column("id", 10), Column("name", 24), Column("role", 12), Column("active", 7)]
    )
    rows = [
        [person.id, person.name, person.role.value, person.active]
        for person in service.people(Role(role) if role else None)
    ]
    for line in table.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} people")


@roster.command("assign")
@click.argument("code")
@click.argument("lane")
@click.argument("staff_id")
@pass_context
def assign(ctx: Context, code: str, lane: str, staff_id: str) -> None:
    """Put somebody on a lane."""
    assignment = RosterService(ctx.open()).assign(code, lane, staff_id)
    click.echo(f"{staff_id} on lane {assignment.lane} as {assignment.role.value}")


@roster.command("unassign")
@click.argument("code")
@click.argument("lane")
@click.argument("staff_id")
@pass_context
def unassign(ctx: Context, code: str, lane: str, staff_id: str) -> None:
    """Take somebody off a lane."""
    RosterService(ctx.open()).unassign(code, lane, staff_id)
    click.echo(f"{staff_id} off lane {lane.upper()}")


@roster.command("lane")
@click.argument("code")
@click.argument("lane")
@pass_context
def show_lane(ctx: Context, code: str, lane: str) -> None:
    """Who is on one lane."""
    service = RosterService(ctx.open())
    for member in service.on_lane(code, lane):
        person = service.person(member.staff_id)
        click.echo(f"  {member.role.value:<12} {person.name}")


@roster.command("gaps")
@click.argument("code")
@pass_context
def gaps(ctx: Context, code: str) -> None:
    """What is stopping each lane opening."""
    service = RosterService(ctx.open())
    short = service.gaps(code)
    for lane, missing in sorted(short.items()):
        click.echo(f"lane {lane} needs {', '.join(missing)}")
    for clash in service.duplicate_check(code):
        click.echo(f"clash: {clash}", err=True)
    click.echo(f"{len(short)} lanes short")
    if short:
        raise SystemExit(1)
