"""``az market`` and ``az limits``.

Both are used by the sales desk on the phone to a seller or a buyer, so both
print the reasoning rather than just the number. A seller told to ask nine
thousand two hundred wants to know why, and "three of these sold here last
month" is an answer they will take.
"""

from __future__ import annotations

from datetime import date

import click

from autozest.block.limits import LimitService, Scope
from autozest.catalog.market import MarketService
from autozest.catalog.vehicles import VehicleRepository
from autozest.cli.context import Context
from autozest.inspection.grade import GradeBand
from autozest.money import Money
from autozest.reporting.text import Align, Column, Table, key_values, money_column

pass_context = click.make_pass_decorator(Context)

BAND_CHOICE = click.Choice([band.value for band in GradeBand])
SCOPE_CHOICE = click.Choice([scope.value for scope in Scope])


@click.group()
def market() -> None:
    """Guide values, comparable sales and what to ask."""


@market.command("guide")
@click.argument("make")
@click.argument("model")
@click.argument("value")
@click.option("--year", "model_year", type=int, required=True, help="Model year.")
@click.option("--band", default="clean", type=BAND_CHOICE, help="Which grade band.")
@click.option("--source", default="guide", show_default=True, help="Where it came from.")
@pass_context
def add_guide(
    ctx: Context, make: str, model: str, model_year: int, band: str, value: str, source: str
) -> None:
    """Load a guide value."""
    MarketService(ctx.open()).add_guide(
        make, model, model_year, GradeBand(band), Money.parse(value), source=source
    )
    click.echo(f"{model_year} {make} {model} {band}: {Money.parse(value)}")


@market.command("advise")
@click.argument("vin")
@click.option("--band", default="clean", type=BAND_CHOICE)
@click.option("--as-at", "as_at", default=None, help="Advise as at a date.")
@pass_context
def advise(ctx: Context, vin: str, band: str, as_at: str | None) -> None:
    """What to tell the seller to ask, and why."""
    conn = ctx.open()
    vehicle = VehicleRepository(conn).get(vin.strip().upper())
    service = MarketService(conn)
    day = date.fromisoformat(as_at) if as_at else None
    advice = service.advise(vehicle, GradeBand(band), as_at=day)
    for line in key_values(
        [
            ("vehicle", vehicle.description),
            ("value", advice.value),
            ("floor to ask", advice.suggested_floor),
            ("from", advice.source),
            ("samples", advice.samples),
        ]
    ):
        click.echo(line)
    comparables = service.comparables(vehicle, as_at=day)
    if comparables:
        click.echo("comparables")
        table = Table(
            [Column("consignment", 13), money_column("hammer", 12), Column("sold", 12)]
        )
        rows: list[list[object]] = [
            [item.consignment_id, item.hammer, item.sold_on] for item in comparables[:10]
        ]
        for text in table.render(rows):
            click.echo(f"  {text}")


@click.group()
def limits() -> None:
    """Caps a buyer asked for, or had imposed."""


@limits.command("set")
@click.argument("buyer")
@click.argument("scope", type=SCOPE_CHOICE)
@click.argument("amount")
@click.option("--reason", required=True, help="Why, for the record.")
@click.option("--by", "set_by", required=True, help="Who is doing it.")
@click.option("--imposed", is_flag=True, help="Set by us rather than asked for.")
@pass_context
def set_limit(
    ctx: Context, buyer: str, scope: str, amount: str, reason: str, set_by: str, imposed: bool
) -> None:
    """Put a cap on a buyer."""
    limit = LimitService(ctx.open()).set_limit(
        buyer,
        Scope(scope),
        Money.parse(amount),
        reason=reason,
        set_by=set_by,
        imposed=imposed,
    )
    click.echo(f"{limit.id}  {limit.describe()}")


@limits.command("lift")
@click.argument("reference")
@pass_context
def lift(ctx: Context, reference: str) -> None:
    """Take a cap off."""
    lifted = LimitService(ctx.open()).lift(reference)
    click.echo(f"{lifted.id} lifted")


@limits.command("show")
@click.argument("buyer")
@pass_context
def show(ctx: Context, buyer: str) -> None:
    """Every cap on one buyer."""
    service = LimitService(ctx.open())
    live = service.all_live(buyer)
    table = Table(
        [
            Column("limit", 13),
            Column("scope", 10),
            money_column("amount", 13),
            Column("imposed", 8, Align.RIGHT),
            Column("reason", 40),
        ]
    )
    rows: list[list[object]] = [
        [item.id, item.scope.value, item.amount, item.imposed, item.reason] for item in live
    ]
    for text in table.render(rows):
        click.echo(text)
    click.echo(f"{len(rows)} live limits")
