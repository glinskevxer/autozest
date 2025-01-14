"""``az sale`` and ``az block``.

Two audiences. ``az sale`` is the block office building next Tuesday: create
the sale, fill the lanes, print the run sheet. ``az block`` is the ring itself,
used live while units are crossing, so its commands are short and every one of
them prints the outcome on a single line an auctioneer can glance at.
"""

from __future__ import annotations

from datetime import date

import click

from autozest.block.bidding import BiddingService
from autozest.block.catalogue import CatalogueService
from autozest.block.models import RunStatus
from autozest.block.readiness import render as render_readiness
from autozest.block.registration import RegistrationService
from autozest.block.sales import SaleRepository
from autozest.cli import lookup
from autozest.cli.context import Context
from autozest.consignment.repository import ConsignmentRepository
from autozest.errors import ValidationError
from autozest.inspection.announcements import Light
from autozest.money import Money
from autozest.reporting.text import Align, Column, Table, key_values, money_column

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


@click.group()
def block() -> None:
    """Live commands for the ring."""


@block.command("bid")
@click.argument("code")
@click.argument("run")
@click.argument("buyer")
@click.argument("amount")
@pass_context
def bid(ctx: Context, code: str, run: str, buyer: str, amount: str) -> None:
    """Take a bid on a run."""
    conn = ctx.open()
    found = lookup.run(ctx, code, run)
    assert found.id is not None
    placed = BiddingService(conn).bid(found.id, buyer, Money.parse(amount))
    click.echo(f"{found.number}  {placed.amount}  {buyer}")


@block.command("fall")
@click.argument("code")
@click.argument("run")
@pass_context
def fall(ctx: Context, code: str, run: str) -> None:
    """Drop the hammer."""
    conn = ctx.open()
    found = lookup.run(ctx, code, run)
    assert found.id is not None
    settled = BiddingService(conn).fall(found.id)
    if settled.status is RunStatus.SOLD:
        click.echo(f"{settled.number} sold {settled.hammer} to {settled.buyer_id}")
    elif settled.status is RunStatus.IF_BID:
        click.echo(f"{settled.number} on if at {settled.hammer}")
    else:
        click.echo(f"{settled.number} no sale")


@block.command("counter")
@click.argument("code")
@click.argument("run")
@click.argument("amount")
@pass_context
def counter(ctx: Context, code: str, run: str, amount: str) -> None:
    """The seller comes back with their own number."""
    conn = ctx.open()
    found = lookup.run(ctx, code, run)
    assert found.id is not None
    made = BiddingService(conn).counter(found.id, Money.parse(amount))
    click.echo(f"{found.number} countered at {made.amount}")


@block.command("accept")
@click.argument("code")
@click.argument("run")
@pass_context
def accept(ctx: Context, code: str, run: str) -> None:
    """Take the standing offer on a unit that is on if."""
    conn = ctx.open()
    found = lookup.run(ctx, code, run)
    assert found.id is not None
    settled = BiddingService(conn).accept(found.id)
    click.echo(f"{settled.number} sold {settled.hammer} to {settled.buyer_id}")


@block.command("decline")
@click.argument("code")
@click.argument("run")
@pass_context
def decline(ctx: Context, code: str, run: str) -> None:
    """Nobody moved, so the unit did not sell."""
    conn = ctx.open()
    found = lookup.run(ctx, code, run)
    assert found.id is not None
    click.echo(f"{BiddingService(conn).decline(found.id).number} no sale")


@block.command("if-list")
@click.argument("code")
@pass_context
def if_list(ctx: Context, code: str) -> None:
    """Everything waiting on a seller's decision."""
    conn = ctx.open()
    consignments = ConsignmentRepository(conn)
    table = Table(
        [
            Column("run", 5),
            Column("consignment", 13),
            money_column("bid", 11),
            money_column("floor", 11),
            Column("short by", 11, Align.RIGHT),
        ]
    )
    rows = []
    for run in BiddingService(conn).pending_if_bids(code):
        unit = consignments.get(run.consignment_id)
        short = (unit.floor - run.hammer) if unit.floor and run.hammer else None
        rows.append([run.number, unit.id, run.hammer, unit.floor, short])
    for text in table.render(rows):
        click.echo(text)
    click.echo(f"{len(rows)} on if")


@block.command("show")
@click.argument("code")
@click.argument("run")
@pass_context
def show_run(ctx: Context, code: str, run: str) -> None:
    """One run, its bids and where it stands."""
    conn = ctx.open()
    found = lookup.run(ctx, code, run)
    assert found.id is not None
    bidding = BiddingService(conn)
    unit = ConsignmentRepository(conn).get(found.consignment_id)
    for text in key_values(
        [
            ("run", found.number),
            ("consignment", unit.id),
            ("vin", unit.vin),
            ("light", found.light.value),
            ("floor", unit.floor),
            ("status", found.status.value),
            ("hammer", found.hammer),
            ("buyer", found.buyer_id),
        ]
    ):
        click.echo(text)
    history = bidding.bids(found.id)
    if history:
        click.echo("bids")
        for placed in history:
            amount = str(placed.amount)
            click.echo(f"  {placed.kind.value:8} {amount:>12} {placed.buyer_id or ''}")


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


@block.command("register")
@click.argument("code")
@click.argument("buyer")
@click.option("--badge", type=int, default=None, help="Ask for a particular badge.")
@click.option("--force", is_flag=True, help="Register anyway and mark the badge blocked.")
@pass_context
def register(ctx: Context, code: str, buyer: str, badge: int | None, force: bool) -> None:
    """Put a buyer on the door list."""
    service = RegistrationService(ctx.open())
    registration = service.register(code, buyer, badge=badge, force=force)
    click.echo(f"badge {registration.badge} for {buyer}, {registration.status.value}")
    if registration.blocked_reason:
        click.echo(f"  {registration.blocked_reason}", err=True)


@block.command("door")
@click.argument("code")
@pass_context
def door(ctx: Context, code: str) -> None:
    """Who is on the door list."""
    service = RegistrationService(ctx.open())
    table = Table(
        [
            Column("badge", 6, Align.RIGHT),
            Column("buyer", 12),
            Column("status", 10),
            Column("reason", 44),
        ]
    )
    rows = [
        [r.badge, r.buyer_id, r.status.value, r.blocked_reason] for r in service.for_sale(code)
    ]
    for line in table.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} registered")


@block.command("blockers")
@click.argument("buyer")
@pass_context
def blockers(ctx: Context, buyer: str) -> None:
    """What would stop this buyer registering."""
    reasons = RegistrationService(ctx.open()).blockers(buyer)
    for reason in reasons:
        click.echo(reason)
    click.echo(f"{len(reasons)} blockers")


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
