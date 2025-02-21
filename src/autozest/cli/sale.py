"""``az sale``.

The block office building next Tuesday: create the sale, fill the lanes, print
the run sheet, and answer whether the thing can open. Separated from ``az
block`` because the two have different users on different days, and one file
holding both had grown past the point where either could be read.
"""

from __future__ import annotations

from datetime import date

import click

from autozest.block.catalogue import CatalogueService
from autozest.block.models import RunStatus
from autozest.block.readiness import render as render_readiness
from autozest.block.sales import SaleRepository
from autozest.block.templates import TemplateService
from autozest.cli import lookup
from autozest.cli.context import Context
from autozest.errors import ValidationError
from autozest.inspection.announcements import Light
from autozest.reporting.text import Column, Table, money_column

pass_context = click.make_pass_decorator(Context)

RUN_TABLE = Table(
    [
        Column("run", 5),
        Column("consignment", 13),
        Column("vin", 17),
        Column("light", 6),
        Column("status", 9),
        money_column("hammer", 11),
        Column("buyer", 8),
    ]
)


def _parse_day(text: str) -> date:
    """A date somebody typed, with an error they can act on.

    date.fromisoformat raises a ValueError whose message names the library
    rather than the field, which is not what a person at a terminal needs.
    """
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{text!r} is not a date, use YYYY-MM-DD") from exc


def _parse_lanes(spec: str) -> dict[str, int]:
    """``A:100,B:80`` into lanes and their capacities."""
    lanes: dict[str, int] = {}
    for part in spec.split(","):
        letter, _, capacity = part.partition(":")
        lanes[letter.strip().upper()] = int(capacity or 100)
    return lanes


@click.group()
def sale() -> None:
    """Plan a sale, catalogue units into it and print the run sheet."""


@sale.command("create")
@click.argument("when")
@click.option("--title", required=True)
@click.option("--lanes", default="A:100", help="Lane letters and capacities, A:100,B:80.")
@pass_context
def create(ctx: Context, when: str, title: str, lanes: str) -> None:
    """Plan a sale for a date."""
    repo = SaleRepository(ctx.open())
    created = repo.create(_parse_day(when), title, _parse_lanes(lanes))
    letters = ", ".join(lane.letter for lane in repo.lanes(created.code))
    click.echo(f"created {created.code} with lanes {letters}")


@sale.command("catalogue")
@click.argument("code")
@click.option("--lane", required=True)
@click.option(
    "--unit", "units", multiple=True, help="Consignment references, or all that are ready."
)
@click.option("--guarantee", is_flag=True, help="The seller stands behind the condition.")
@pass_context
def catalogue(
    ctx: Context, code: str, lane: str, units: tuple[str, ...], guarantee: bool
) -> None:
    """Put units into a lane, deciding each light on the way in."""
    service = CatalogueService(ctx.open())
    if units and guarantee:
        placed = [service.place(code, unit, lane, guaranteed=True) for unit in units]
    else:
        placed = service.fill(code, lane, list(units) or None)
    for placement in placed:
        click.echo(
            f"{placement.lane}{placement.position:03d}  {placement.consignment.id}  "
            f"{placement.decision.explain()}"
        )
    click.echo(f"{len(placed)} units catalogued into lane {lane.upper()}")


@sale.command("runs")
@click.argument("code")
@click.option("--lane", default=None)
@pass_context
def runs(ctx: Context, code: str, lane: str | None) -> None:
    """The run sheet."""
    repo = SaleRepository(ctx.open())
    found = repo.get(code)
    click.echo(f"{found.code}  {found.title}  {found.sale_date}  {found.status.value}")
    rows = [
        [
            line.number,
            line.consignment_id,
            line.vin,
            line.light.value,
            line.status.value,
            line.hammer,
            line.buyer_id,
        ]
        for line in repo.run_sheet(code, lane.upper() if lane else None)
    ]
    for text in RUN_TABLE.render(rows):
        click.echo(text)
    click.echo(f"{len(rows)} runs")


@sale.command("open")
@click.argument("code")
@pass_context
def open_sale(ctx: Context, code: str) -> None:
    """Freeze the catalogue and start selling."""
    click.echo(f"{SaleRepository(ctx.open()).open_sale(code).code} is open")


@sale.command("close")
@click.argument("code")
@pass_context
def close_sale(ctx: Context, code: str) -> None:
    """Close the sale. Anything left scheduled becomes a no sale."""
    repo = SaleRepository(ctx.open())
    closed = repo.close_sale(code)
    sold = sum(1 for run in repo.runs(code) if run.status is RunStatus.SOLD)
    click.echo(f"{closed.code} closed, {sold} sold of {len(repo.runs(code))}")


@sale.command("list")
@pass_context
def list_sales(ctx: Context) -> None:
    """Recent sales, newest first."""
    table = Table(
        [Column("code", 11), Column("date", 10), Column("title", 30), Column("status", 9)]
    )
    rows = [
        [s.code, s.sale_date, s.title, s.status.value]
        for s in SaleRepository(ctx.open()).recent()
    ]
    for text in table.render(rows):
        click.echo(text)


@sale.command("relight")
@click.argument("code")
@click.argument("run")
@click.argument("light", type=click.Choice([light.value for light in Light]))
@click.argument("reason")
@pass_context
def relight(ctx: Context, code: str, run: str, light: str, reason: str) -> None:
    """Move a light by hand, before the sale opens."""
    conn = ctx.open()
    found = lookup.run(ctx, code, run)
    assert found.id is not None
    moved = CatalogueService(conn).relight(found.id, Light(light), reason)
    click.echo(f"{moved.number} is now {moved.light.value}")


@sale.command("ready")
@click.argument("code")
@pass_context
def ready(ctx: Context, code: str) -> None:
    """What is stopping this sale opening.

    Ordered by how long each one takes to fix, so it reads as a list somebody
    works through rather than one they read.
    """
    lines = render_readiness(ctx.open(), code)
    for line in lines:
        click.echo(line)
    if "must be done first" in lines[-1]:
        raise SystemExit(1)


@sale.command("template")
@click.argument("action", type=click.Choice(["add", "list", "build", "next", "retire"]))
@click.option("--id", "template_id", default=None)
@click.option("--name", default=None)
@click.option("--weekday", type=click.IntRange(0, 6), default=None, help="0 is Monday.")
@click.option("--title", default=None)
@click.option("--lanes", default="A:100", help="Lane letters and capacities, A:100,B:80.")
@click.option("--on", "on_date", default=None, help="The date to build, for build.")
@pass_context
def template(
    ctx: Context,
    action: str,
    template_id: str | None,
    name: str | None,
    weekday: int | None,
    title: str | None,
    lanes: str,
    on_date: str | None,
) -> None:
    """The sales that run every week, and building one from them."""
    service = TemplateService(ctx.open())
    if action == "list":
        for found, when in service.upcoming():
            click.echo(f"{found.id:<8} {found.describe()}  next {when}")
        return
    if action == "add":
        if not (template_id and name and title and weekday is not None):
            raise click.UsageError("add needs --id, --name, --weekday and --title")
        made = service.create(template_id, name, weekday, title, _parse_lanes(lanes))
        click.echo(made.describe())
        return
    if not template_id:
        raise click.UsageError(f"{action} needs --id")
    if action == "retire":
        click.echo(f"{service.retire(template_id).name} retired")
        return
    built = (
        service.build(template_id, _parse_day(on_date))
        if action == "build" and on_date
        else service.build_next(template_id)
    )
    click.echo(f"created {built.code}  {built.title}  {built.sale_date}")
