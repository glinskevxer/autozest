"""``az admin``.

Housekeeping. Nothing here is part of a sale night and everything here is
something somebody needs at half past six in the morning when the block office
machine will not start.

``backup`` uses SQLite's own online backup rather than copying the file,
because copying a database that is open produces something that looks fine and
is not. That has cost this business a Tuesday already.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import click

from autozest.audit import AuditLog
from autozest.cli.context import Context
from autozest.reporting.text import Align, Column, Table, key_values
from autozest.storage.db import connect, integrity_problems, table_names
from autozest.storage.migrator import current_version, load_migrations, pending
from autozest.tools.check_migrations import problems as migration_problems
from autozest.tools.demo import build as build_demo

pass_context = click.make_pass_decorator(Context)


@click.group()
def admin() -> None:
    """Backups, checks and the audit trail."""


@admin.command("backup")
@click.argument("target", required=False)
@pass_context
def backup(ctx: Context, target: str | None) -> None:
    """Copy the database safely, using sqlite's own backup.

    Copying an open database file produces something that looks fine and is
    not, which is a lesson this building has already paid for.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    default_name = f"{ctx.path.stem}-{stamp}.sqlite3"
    destination = Path(target) if target else ctx.path.with_name(default_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = ctx.open()
    copy: sqlite3.Connection = connect(destination)
    with copy:
        source.backup(copy)
    copy.close()
    size = destination.stat().st_size
    click.echo(f"{destination}  {size} bytes")


@admin.command("vacuum")
@pass_context
def vacuum(ctx: Context) -> None:
    """Reclaim space after a big archive."""
    conn = ctx.open()
    before = ctx.path.stat().st_size if ctx.path.exists() else 0
    conn.execute("VACUUM")
    after = ctx.path.stat().st_size if ctx.path.exists() else 0
    click.echo(f"{before} bytes to {after}")


@admin.command("check")
@pass_context
def check(ctx: Context) -> None:
    """Everything that can be checked without changing anything."""
    conn = ctx.open()
    found: list[str] = []
    found.extend(integrity_problems(conn))
    found.extend(migration_problems())
    outstanding = pending(conn)
    if outstanding:
        found.append(f"{len(outstanding)} migrations have not been applied")
    for problem in found:
        click.echo(f"problem: {problem}", err=True)
    click.echo(f"{len(found)} problems")
    if found:
        raise SystemExit(1)


@admin.command("stats")
@pass_context
def stats(ctx: Context) -> None:
    """How big everything is, for somebody deciding whether to archive."""
    conn = ctx.open()
    table = Table([Column("table", 26), Column("rows", 10, Align.RIGHT)])
    rows: list[list[object]] = []
    for name in table_names(conn):
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
        rows.append([name, count])
    for line in table.render(rows):
        click.echo(line)
    for line in key_values(
        [
            ("schema", f"{current_version(conn)} of {len(load_migrations())}"),
            ("file", ctx.path),
            ("bytes", ctx.path.stat().st_size if ctx.path.exists() else 0),
        ]
    ):
        click.echo(line)


@admin.command("audit")
@click.option("--actor", default=None)
@click.option("--subject", default=None, help="A kind of record, such as consignment.")
@click.option("--id", "subject_id", default=None)
@click.option("--limit", default=30, show_default=True)
@pass_context
def audit(
    ctx: Context, actor: str | None, subject: str | None, subject_id: str | None, limit: int
) -> None:
    """Read the audit trail."""
    log = AuditLog(ctx.open())
    if subject and subject_id:
        events = log.for_subject(subject, subject_id)
    elif actor:
        events = log.by_actor(actor, limit)
    else:
        events = log.recent(limit)
    for event in events:
        click.echo(str(event))
    click.echo(f"{len(events)} events of {log.count()}")


@admin.command("note")
@click.argument("subject")
@click.argument("subject_id")
@click.argument("detail")
@click.option("--actor", default=None)
@pass_context
def note(ctx: Context, subject: str, subject_id: str, detail: str, actor: str | None) -> None:
    """Put a line on the trail by hand."""
    log = AuditLog(ctx.open(), actor)
    log.record("note", subject, subject_id, detail)
    click.echo(f"noted against {subject} {subject_id}")


@admin.command("seed")
@click.option("--units", default=24, show_default=True)
@click.option("--seed", "seed_value", default=20240312, show_default=True)
@click.option("--date", "sale_day", default=None, help="Sale date, defaults to 2024-03-12.")
@pass_context
def seed(ctx: Context, units: int, seed_value: int, sale_day: str | None) -> None:
    """Fill an empty database with one sale's worth of everything.

    For a demonstration or a load test. The same seed builds the same sale, so
    a bug that only shows on the two hundredth unit is reproducible.
    """
    conn = ctx.open(create=True)
    if conn.execute("SELECT COUNT(*) AS n FROM account").fetchone()["n"]:
        click.echo("this database already has accounts in it", err=True)
        raise SystemExit(5)
    built = build_demo(
        conn,
        units=units,
        seed=seed_value,
        sale_day=date.fromisoformat(sale_day) if sale_day else None,
    )
    click.echo(
        f"{built.sale_code}  {built.units} units, {built.sold} sold, "
        f"{built.no_sale} no sale, {built.accounts} accounts"
    )
