"""``az title``.

The title desk is one person with a filing cabinet and a scanner, so these
commands are built around what is physically in their hand. ``receive`` takes
the brand off the document in front of them and says immediately whether it
matches what was called at the block.
"""

from __future__ import annotations

import click

from autozest.cli.context import Context
from autozest.consignment.intake import IntakeService
from autozest.reporting.text import Column, Table, key_values
from autozest.title.branding import TitleBrand
from autozest.title.service import TitleService, TitleStatus

pass_context = click.make_pass_decorator(Context)

BRAND_CHOICE = click.Choice([brand.value for brand in TitleBrand])

TITLE_TABLE = Table(
    [
        Column("consignment", 13),
        Column("status", 13),
        Column("brand", 20),
        Column("state", 5),
        Column("number", 14),
    ]
)


def _consignment(ctx: Context, token: str) -> str:
    return IntakeService(ctx.open()).find(token).id


@click.group()
def title() -> None:
    """Log titles in, chase the missing ones and send them out."""


@title.command("expect")
@click.argument("token")
@click.option("--actor", default="title")
@pass_context
def expect(ctx: Context, token: str, actor: str) -> None:
    """Open a title record on a consigned unit."""
    service = TitleService(ctx.open())
    record = service.expect(_consignment(ctx, token), actor=actor)
    click.echo(f"{record.consignment_id} title is {record.status.value}")


@title.command("receive")
@click.argument("token")
@click.option("--brand", default="clean", type=BRAND_CHOICE)
@click.option("--state", "issuing_state", default=None)
@click.option("--number", "title_number", default=None)
@click.option("--actor", default="title")
@pass_context
def receive(
    ctx: Context,
    token: str,
    brand: str,
    issuing_state: str | None,
    title_number: str | None,
    actor: str,
) -> None:
    """Log a title in off the document in front of you."""
    service = TitleService(ctx.open())
    record, surprise = service.receive(
        _consignment(ctx, token),
        brand=TitleBrand(brand),
        issuing_state=issuing_state,
        title_number=title_number,
        actor=actor,
    )
    click.echo(f"{record.consignment_id} title on hand, brand {record.brand.value}")
    if surprise is not None:
        click.echo(
            f"undisclosed {surprise.value} brand, this sale unwinds regardless of any window",
            err=True,
        )
        raise SystemExit(7)


@title.command("mark")
@click.argument("token")
@click.argument("status", type=click.Choice([s.value for s in TitleStatus]))
@click.option("--detail", default=None)
@click.option("--actor", default="title")
@pass_context
def mark(ctx: Context, token: str, status: str, detail: str | None, actor: str) -> None:
    """Move a title record, for a duplicate application or an attorney lane."""
    service = TitleService(ctx.open())
    record = service.mark(
        _consignment(ctx, token), TitleStatus(status), actor=actor, detail=detail
    )
    click.echo(f"{record.consignment_id} title is {record.status.value}")


@title.command("send")
@click.argument("token")
@click.option("--actor", default="title")
@pass_context
def send(ctx: Context, token: str, actor: str) -> None:
    """Send a title to the buyer, if nothing is holding it."""
    service = TitleService(ctx.open())
    record = service.send(_consignment(ctx, token), actor=actor)
    click.echo(f"{record.consignment_id} title sent to the buyer")


@title.command("release")
@click.argument("token")
@click.option("--actor", default="title")
@pass_context
def release(ctx: Context, token: str, actor: str) -> None:
    """Give a title back on a unit that did not sell."""
    service = TitleService(ctx.open())
    record = service.release_to_seller(_consignment(ctx, token), actor=actor)
    click.echo(f"{record.consignment_id} title back to the seller")


@title.command("holds")
@click.argument("token")
@pass_context
def holds(ctx: Context, token: str) -> None:
    """Why a title cannot go out yet."""
    service = TitleService(ctx.open())
    consignment_id = _consignment(ctx, token)
    reasons = service.holds(consignment_id)
    for reason in reasons:
        click.echo(reason)
    click.echo(f"{len(reasons)} holds")


@title.command("show")
@click.argument("token")
@pass_context
def show(ctx: Context, token: str) -> None:
    """One title and everywhere it has been."""
    service = TitleService(ctx.open())
    consignment_id = _consignment(ctx, token)
    record = service.get(consignment_id)
    for line in key_values(
        [
            ("consignment", record.consignment_id),
            ("status", record.status.value),
            ("brand", record.brand.value),
            ("state", record.issuing_state),
            ("number", record.title_number),
            ("received", record.received_on),
            ("released", record.released_on),
            ("blocks retail", record.brand.blocks_retail),
        ]
    ):
        click.echo(line)
    click.echo("movements")
    for movement in service.movements(consignment_id):
        was = movement.from_status.value if movement.from_status else "new"
        detail = movement.detail or ""
        click.echo(f"  {was} to {movement.to_status.value}  {movement.actor}  {detail}")


@title.command("outstanding")
@pass_context
def outstanding(ctx: Context) -> None:
    """Every title still being waited on."""
    service = TitleService(ctx.open())
    records = service.outstanding()
    rows = [
        [r.consignment_id, r.status.value, r.brand.value, r.issuing_state, r.title_number]
        for r in records
    ]
    for line in TITLE_TABLE.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} outstanding")
