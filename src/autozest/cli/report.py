"""``az report``.

Everything printed rather than changed. These commands never write, which is
why the block office can leave one running on a loop against the live database
on a sale night without anyone worrying about it.
"""

from __future__ import annotations

import click

from autozest.cli.context import Context
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
