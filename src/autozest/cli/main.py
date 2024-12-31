"""Entry point for ``az``.

Commands stay thin. They parse what the operator typed, hand it to the domain
and render what comes back. Every domain error is caught in one place and
turned into a message and an exit status, so no command has to remember to do
it and the statuses cannot drift apart.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import click

from autozest import __version__
from autozest.cli.admin import admin
from autozest.cli.arb import arb
from autozest.cli.block import block, sale
from autozest.cli.consign import consign, inspect
from autozest.cli.context import Context
from autozest.cli.report import report
from autozest.cli.settle import settle
from autozest.cli.staff import roster
from autozest.cli.title import title
from autozest.cli.trail import is_worth_recording, record, subject_of
from autozest.cli.yard import gate, yard
from autozest.errors import AutozestError, exit_status_for
from autozest.logs import LEVELS, configure, log
from autozest.storage.db import connect, integrity_problems
from autozest.storage.migrator import current_version, load_migrations, pending

pass_context = click.make_pass_decorator(Context)


@click.group()
@click.option("--config", "config_path", default=None, help="Path to autozest.toml.")
@click.option("--db", "database", default=None, help="Path to the database file.")
@click.option(
    "--log",
    "log_level",
    default="warning",
    type=click.Choice(sorted(LEVELS)),
    show_default=True,
    help="How much to say on stderr.",
)
@click.version_option(__version__, prog_name="az")
@click.pass_context
def cli(
    ctx: click.Context, config_path: str | None, database: str | None, log_level: str
) -> None:
    """Consignment, block and settlement for a wholesale vehicle auction."""
    configure(log_level)
    ctx.obj = Context.build(config_path, database)
    log.debug("cli.start", command=ctx.invoked_subcommand, db=database)
    ctx.call_on_close(ctx.obj.close)


@cli.command()
@pass_context
def init(ctx: Context) -> None:
    """Create the database and bring the schema up to date."""
    version = ctx.initialise()
    click.echo(f"{ctx.path} is at schema version {version}")


@cli.command()
@pass_context
def status(ctx: Context) -> None:
    """Report the schema version and anything outstanding."""
    conn = ctx.open()
    version = current_version(conn)
    outstanding = pending(conn)
    click.echo(f"auction   {ctx.settings.auction_name} ({ctx.settings.auction_code})")
    click.echo(f"database  {ctx.path}")
    click.echo(f"schema    {version} of {len(load_migrations())}")
    if outstanding:
        names = ", ".join(m.filename for m in outstanding)
        click.echo(f"pending   {names}")


@cli.command()
@pass_context
def doctor(ctx: Context) -> None:
    """Check the database file for damage and orphaned rows."""
    problems = integrity_problems(ctx.open())
    for problem in problems:
        click.echo(f"problem: {problem}", err=True)
    if problems:
        raise SystemExit(1)
    click.echo("no problems")


cli.add_command(consign)
cli.add_command(inspect)
cli.add_command(sale)
cli.add_command(block)
cli.add_command(report)
cli.add_command(arb)
cli.add_command(title)
cli.add_command(settle)
cli.add_command(admin)
cli.add_command(yard)
cli.add_command(gate)
cli.add_command(roster)


def run(argv: list[str] | None = None, **extra: Any) -> int:
    """Run the command line, record what it did, and map errors to statuses.

    The audit line goes on after the work succeeded. A command that raised
    changed nothing worth recording, and a trail full of attempts is a trail
    nobody reads.
    """
    arguments = list(argv or [])
    try:
        cli.main(args=arguments, standalone_mode=False, **extra)
        _record(arguments)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        click.echo("aborted", err=True)
        return 130
    except AutozestError as exc:
        log.error("cli.failed", code=exc.code, message=exc.message, **exc.context)
        click.echo(f"error: {exc}", err=True)
        return exit_status_for(exc)
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:
        log.error("cli.crashed", kind=type(exc).__name__, message=str(exc))
        click.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        return 1
    return 0


def main() -> int:
    return run(sys.argv[1:])


def _record(arguments: list[str]) -> None:
    """Put one line on the trail for a command that changed something."""
    positional = [item for item in arguments if not item.startswith("-")]
    skip = {"--db", "--config", "--log"}
    cleaned: list[str] = []
    index = 0
    while index < len(arguments):
        if arguments[index] in skip:
            index += 2
            continue
        cleaned.append(arguments[index])
        index += 1
    positional = [item for item in cleaned if not item.startswith("-")]
    if len(positional) < 2:
        return
    group, command = positional[0], positional[1]
    if not is_worth_recording(group, command):
        return
    database = _database_from(arguments)
    if database is None:
        return
    try:
        conn = connect(database, create=False)
    except AutozestError:
        return
    record(conn, group, command, subject_of(positional[2:]))
    conn.close()


def _database_from(arguments: list[str]) -> str | None:
    for index, item in enumerate(arguments):
        if item == "--db" and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


#: Kept so tests can reach the group without importing click themselves.
group: Callable[..., Any] = cli
