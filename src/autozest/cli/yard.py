"""``az yard`` and ``az gate``.

Both are used on a handheld in the rain, so every command is one line in and
one line out. ``find`` is the one that matters: somebody in a lane needs to
know where a unit is and they need it in under two seconds.
"""

from __future__ import annotations

import click

from autozest.cli.context import Context
from autozest.consignment.intake import IntakeService
from autozest.consignment.release import ReleaseService
from autozest.consignment.yard import Space, SpaceStatus, YardService
from autozest.reporting.text import Align, Column, Table, key_values

pass_context = click.make_pass_decorator(Context)


def _consignment(ctx: Context, token: str) -> str:
    return IntakeService(ctx.open()).find(token).id


@click.group()
def yard() -> None:
    """Spaces, and where the units are."""


@yard.command("add-space")
@click.argument("space_id")
@click.option("--zone", required=True)
@click.option("--row", "row_label", required=True)
@click.option("--number", type=int, required=True)
@click.option("--covered", is_flag=True)
@pass_context
def add_space(
    ctx: Context, space_id: str, zone: str, row_label: str, number: int, covered: bool
) -> None:
    """Add a parking space."""
    service = YardService(ctx.open())
    space = service.add_space(
        Space(id=space_id, zone=zone, row_label=row_label, number=number, covered=covered)
    )
    click.echo(f"{space.id}  {space.label}")


@yard.command("put")
@click.argument("token")
@click.argument("space_id")
@click.option("--by", "moved_by", required=True)
@click.option("--reason", default=None)
@pass_context
def put(ctx: Context, token: str, space_id: str, moved_by: str, reason: str | None) -> None:
    """Park a unit."""
    service = YardService(ctx.open())
    consignment_id = _consignment(ctx, token)
    service.put(consignment_id, space_id, moved_by, reason)
    click.echo(f"{consignment_id} is in {service.space(space_id).label}")


@yard.command("find")
@click.argument("token")
@pass_context
def find(ctx: Context, token: str) -> None:
    """Where is it."""
    service = YardService(ctx.open())
    consignment_id = _consignment(ctx, token)
    space_id = service.where_is(consignment_id)
    if space_id is None:
        click.echo(f"{consignment_id} is not in the yard")
        raise SystemExit(4)
    click.echo(f"{consignment_id} is in {service.space(space_id).label}")


@yard.command("out")
@click.argument("token")
@click.option("--by", "moved_by", required=True)
@click.option("--reason", required=True)
@pass_context
def out(ctx: Context, token: str, moved_by: str, reason: str) -> None:
    """Record a unit leaving the yard."""
    service = YardService(ctx.open())
    consignment_id = _consignment(ctx, token)
    service.take_out(consignment_id, moved_by, reason)
    click.echo(f"{consignment_id} has left the yard")


@yard.command("free")
@click.option("--zone", default=None)
@click.option("--covered", is_flag=True)
@pass_context
def free(ctx: Context, zone: str | None, covered: bool) -> None:
    """Spaces that can take a unit."""
    service = YardService(ctx.open())
    spaces = service.free_spaces(zone, covered=True if covered else None)
    for space in spaces[:40]:
        click.echo(space.label)
    click.echo(f"{len(spaces)} free of {service.capacity()}")


@yard.command("occupancy")
@pass_context
def occupancy(ctx: Context) -> None:
    """How full the yard is."""
    service = YardService(ctx.open())
    counts = service.occupancy()
    for line in key_values([(status.value, counts.get(status, 0)) for status in SpaceStatus]):
        click.echo(line)
    click.echo(f"{service.capacity()} spaces")


@yard.command("history")
@click.argument("token")
@pass_context
def history(ctx: Context, token: str) -> None:
    """Everywhere a unit has been."""
    service = YardService(ctx.open())
    consignment_id = _consignment(ctx, token)
    table = Table(
        [
            Column("when", 20),
            Column("space", 12),
            Column("by", 12),
            Column("reason", 36),
        ]
    )
    rows = []
    for movement in service.history(consignment_id):
        label = service.space(movement.space_id).label if movement.space_id else "off site"
        rows.append(
            [f"{movement.moved_at:%Y-%m-%d %H:%M}", label, movement.moved_by, movement.reason]
        )
    for line in table.render(rows):
        click.echo(line)


@click.group()
def gate() -> None:
    """Keys and letting units off the site."""


@gate.command("keys-in")
@click.argument("token")
@click.argument("tag")
@click.option("--count", type=int, default=2, show_default=True)
@click.option("--location", default="cabinet", show_default=True)
@pass_context
def keys_in(ctx: Context, token: str, tag: str, count: int, location: str) -> None:
    """Log a set of keys into the cabinet."""
    service = ReleaseService(ctx.open())
    keys = service.take_keys(_consignment(ctx, token), tag, count, location)
    click.echo(f"{keys.count} keys on tag {keys.tag} in {keys.location}")


@gate.command("keys-out")
@click.argument("token")
@click.argument("to")
@pass_context
def keys_out(ctx: Context, token: str, to: str) -> None:
    """Hand the keys across."""
    service = ReleaseService(ctx.open())
    keys = service.hand_over(_consignment(ctx, token), to)
    click.echo(f"tag {keys.tag} to {to}")


@gate.command("cabinet")
@pass_context
def cabinet(ctx: Context) -> None:
    """What is still in the cabinet."""
    service = ReleaseService(ctx.open())
    table = Table(
        [
            Column("tag", 10),
            Column("consignment", 13),
            Column("keys", 5, Align.RIGHT),
            Column("where", 14),
        ]
    )
    rows = [[k.tag, k.consignment_id, k.count, k.location] for k in service.held()]
    for line in table.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} sets held")


@gate.command("release")
@click.argument("token")
@click.argument("to")
@click.option("--by", "authorised_by", required=True)
@click.option("--override", default=None, help="Reason for going past a hold.")
@pass_context
def release(
    ctx: Context, token: str, to: str, authorised_by: str, override: str | None
) -> None:
    """Let a unit off the site."""
    service = ReleaseService(ctx.open())
    released = service.release(_consignment(ctx, token), to, authorised_by, override=override)
    click.echo(f"{released.id} released to {to}")
    if released.was_overridden:
        click.echo(f"  overridden: {released.holds_overridden}", err=True)


@gate.command("overrides")
@pass_context
def overrides(ctx: Context) -> None:
    """Everything that went out past a hold."""
    service = ReleaseService(ctx.open())
    found = service.overrides()
    for release_record in found:
        click.echo(
            f"{release_record.id}  {release_record.consignment_id}  "
            f"{release_record.authorised_by}  {release_record.holds_overridden}"
        )
    click.echo(f"{len(found)} overrides")
