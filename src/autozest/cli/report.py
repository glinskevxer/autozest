"""``az report``.

Everything printed rather than changed. These commands never write, which is
why the block office can leave one running on a loop against the live database
on a sale night without anyone worrying about it.
"""

from __future__ import annotations

import click

from autozest.cli.context import Context
from autozest.reporting import aging, exports, reference, turnaround
from autozest.reporting import recap as recap_report
from autozest.reporting.performance import PerformanceService
from autozest.reporting.text import Align, Column, Table, money_column
from autozest.settlement.statements import Side

pass_context = click.make_pass_decorator(Context)


@click.group()
def report() -> None:
    """Print a report. Nothing here writes to the database."""


@report.command("recap")
@click.argument("code")
@pass_context
def recap(ctx: Context, code: str) -> None:
    """The sale recap, printed the way the block office wants it."""
    for line in recap_report.render(ctx.open(), code):
        click.echo(line)


@report.command("export")
@click.argument("name", type=click.Choice(list(exports.available())))
@click.option("--sale", "sale_code", default=None, help="Narrow to one sale.")
@click.option("--out", "out_path", default=None, help="Write to a file rather than stdout.")
@pass_context
def export(ctx: Context, name: str, sale_code: str | None, out_path: str | None) -> None:
    """Write one of the CSV exports."""
    builder = exports.named(name)
    built = (
        builder(ctx.open(), sale_code) if name in ("sold", "ledger") else builder(ctx.open())
    )
    if out_path:
        written = built.write(out_path)
        click.echo(f"{built.row_count} rows to {written}")
        return
    click.echo(built.to_text(), nl=False)


@report.command("exports")
def list_exports() -> None:
    """What can be exported."""
    for name in exports.available():
        click.echo(name)


@report.command("aging")
@click.option("--side", default="buyer", type=click.Choice([s.value for s in Side]))
@pass_context
def aged(ctx: Context, side: str) -> None:
    """Who owes what, and for how long."""
    built = aging.build(ctx.open(), Side(side))
    table = Table(
        [Column("party", 12), money_column("balance", 13), Column("oldest", 7, Align.RIGHT)]
        + [money_column(name, 12) for name in aging.bucket_names()]
    )
    rows: list[list[object]] = [
        [party.party_id, party.balance, party.oldest]
        + [party.bucket(name) for name in aging.bucket_names()]
        for party in built.parties
    ]
    for line in table.render(rows):
        click.echo(line)
    totals = built.totals()
    click.echo(f"{len(rows)} accounts, {built.total} outstanding, {len(built.overdue)} overdue")
    for name in aging.bucket_names():
        click.echo(f"  {name:<10} {totals[name]}")


@report.command("sellers")
@click.option("--sale", "sale_code", default=None)
@pass_context
def sellers(ctx: Context, sale_code: str | None) -> None:
    """Sell through, the gap to the floor, and how often units come back."""
    table = Table(
        [
            Column("seller", 12),
            Column("ran", 5, Align.RIGHT),
            Column("sold", 5, Align.RIGHT),
            Column("through", 8, Align.RIGHT),
            money_column("gross", 14),
            money_column("floor gap", 13),
            Column("arb", 6, Align.RIGHT),
            Column("watch", 6),
        ]
    )
    records = PerformanceService(ctx.open()).sellers(sale_code)
    rows: list[list[object]] = [
        [
            r.seller_id,
            r.ran,
            r.sold,
            f"{r.sell_through}%",
            r.gross,
            r.floor_gap,
            f"{r.arbitration_rate}%",
            r.is_optimistic,
        ]
        for r in records
    ]
    for line in table.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} sellers")


@report.command("buyers")
@click.option("--sale", "sale_code", default=None)
@pass_context
def buyers(ctx: Context, sale_code: str | None) -> None:
    """Who bought what."""
    table = Table(
        [
            Column("buyer", 12),
            Column("bought", 7, Align.RIGHT),
            money_column("spend", 14),
            money_column("average", 13),
            Column("claims", 7, Align.RIGHT),
        ]
    )
    records = PerformanceService(ctx.open()).buyers(sale_code)
    rows: list[list[object]] = [
        [r.buyer_id, r.bought, r.spend, r.average, r.claims] for r in records
    ]
    for line in table.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} buyers")


@report.command("reference")
@click.argument("section", required=False, type=click.Choice(sorted(reference.SECTIONS)))
@pass_context
def rules(ctx: Context, section: str | None) -> None:
    """The rule card, printed from the rules themselves.

    Every laminated card on a counter is out of date. This one cannot be: if
    the window here is wrong then the system is wrong, which is a much better
    problem than a printout nobody trusts.
    """
    if section:
        for line in reference.SECTIONS[section]():
            click.echo(line)
        return
    for line in reference.render():
        click.echo(line)


@report.command("turnaround")
@pass_context
def turnaround_report(ctx: Context) -> None:
    """How long claims and titles take. Medians, not means."""
    for line in turnaround.render(ctx.open(), ctx.settings.calendar):
        click.echo(line)
