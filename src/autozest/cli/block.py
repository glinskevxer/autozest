"""``az block``.

The ring itself, used live while units are crossing. Every command prints its
outcome on one line an auctioneer can glance at, and nothing here takes longer
than a run.
"""

from __future__ import annotations

import click

from autozest.block.bidding import BiddingService
from autozest.block.models import RunStatus
from autozest.block.registration import RegistrationService
from autozest.cli import lookup
from autozest.cli.context import Context
from autozest.consignment.repository import ConsignmentRepository
from autozest.money import Money
from autozest.reporting.text import Align, Column, Table, key_values, money_column

pass_context = click.make_pass_decorator(Context)


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
