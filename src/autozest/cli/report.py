"""``az report``.

Everything printed rather than changed. These commands never write, which is
why the block office can leave one running on a loop against the live database
on a sale night without anyone worrying about it.
"""

from __future__ import annotations

import click

from autozest.cli.context import Context
from autozest.reporting import exports
from autozest.reporting import recap as recap_report

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
