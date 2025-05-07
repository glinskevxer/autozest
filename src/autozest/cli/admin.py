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
from autozest.health import examine
from autozest.history import current as current_release
from autozest.history import render as render_history
from autozest.reporting.manual import render as render_manual
from autozest.reporting.text import Align, Column, Table, key_values
from autozest.retention import archive_sale, prune
from autozest.retention import candidates as retention_candidates
from autozest.storage.db import connect, integrity_problems, table_names
from autozest.storage.migrator import current_version, load_migrations, pending
from autozest.tools.check_migrations import problems as migration_problems
from autozest.tools.demo import build as build_demo
from autozest.tools.doctor import examine as examine_database
from autozest.tools.doctor import repair as repair_database
from autozest.tools.explain import plans
from autozest.tools.redact import redact, would_change

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


@admin.command("health")
@click.option("--soft/--no-soft", default=True, help="Include the things that are not urgent.")
@pass_context
def health(ctx: Context, soft: bool) -> None:
    """Can the building open this morning."""
    report = examine(ctx.open(), ctx.settings)
    for check in report.checks:
        if not soft and check.severity.value == "soft" and check.passed:
            continue
        click.echo(str(check))
    click.echo(report.summary())
    if not report.can_open:
        raise SystemExit(1)


@admin.command("config")
@click.option("--show", is_flag=True, help="Print what was loaded.")
@pass_context
def show_config(ctx: Context, show: bool) -> None:
    """Check the configuration file loads, and what it says."""
    settings = ctx.settings
    for line in key_values(
        [
            ("auction", f"{settings.auction_name} ({settings.auction_code})"),
            ("database", settings.database),
            ("opens", settings.opens),
            ("closes", settings.closes),
            ("closures", len(settings.extra_closures)),
            ("openings", len(settings.extra_openings)),
            ("sections", ", ".join(sorted(settings.sections)) or "none"),
        ]
    ):
        click.echo(line)
    if show:
        for name in sorted(settings.sections):
            click.echo(f"[{name}]")
            for key, value in sorted(settings.section(name).items()):
                click.echo(f"  {key} = {value}")


@admin.command("retention")
@click.option("--apply", "really", is_flag=True, help="Actually delete. Default is a dry run.")
@pass_context
def retention(ctx: Context, really: bool) -> None:
    """What is old enough to prune, and optionally prune it."""
    conn = ctx.open()
    table = Table(
        [
            Column("table", 20),
            Column("rows", 10, Align.RIGHT),
            Column("keep days", 10, Align.RIGHT),
            Column("cutoff", 12),
        ]
    )
    rows = [[c.table, c.rows, c.keep_days, c.cutoff] for c in retention_candidates(conn)]
    for line in table.render(rows):
        click.echo(line)
    result = prune(conn, dry_run=not really)
    verb = "removed" if really else "would remove"
    click.echo(f"{verb} {result.total} rows across {len(result.removed)} tables")


@admin.command("archive")
@click.argument("code")
@click.argument("target")
@pass_context
def archive(ctx: Context, code: str, target: str) -> None:
    """Copy a closed sale out to its own file. Removes nothing."""
    written = archive_sale(ctx.open(), code, target)
    for name, count in sorted(written.tables.items()):
        click.echo(f"  {name:<14} {count}")
    click.echo(f"{written.rows} rows to {written.path}")


@admin.command("sizes")
@click.option("--top", default=10, show_default=True)
@pass_context
def sizes(ctx: Context, top: int) -> None:
    """Which tables are actually taking the space.

    Row counts alone mislead. A million audit lines of forty characters are
    smaller than ten thousand notice bodies, and the person deciding what to
    archive needs the second number.
    """
    conn = ctx.open()
    measured: list[tuple[str, int, int]] = []
    for name in table_names(conn):
        count = int(conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"])
        payload = int(
            conn.execute(
                f"SELECT COALESCE(SUM(LENGTH(CAST(t.rowid AS TEXT))), 0) AS n FROM {name} t"
            ).fetchone()["n"]
        )
        measured.append((name, count, payload))
    measured.sort(key=lambda row: -row[1])
    rows: list[list[object]] = [[name, count, payload] for name, count, payload in measured]
    table = Table(
        [
            Column("table", 26),
            Column("rows", 10, Align.RIGHT),
            Column("key bytes", 12, Align.RIGHT),
        ]
    )
    for line in table.render(rows[:top]):
        click.echo(line)
    click.echo(f"{len(measured)} tables, {sum(count for _, count, _ in measured)} rows")


@admin.command("plans")
@pass_context
def query_plans(ctx: Context) -> None:
    """What sqlite is doing with the queries that run on a screen."""
    found = plans(ctx.open())
    for plan in found:
        click.echo(str(plan))
    scanning = [plan for plan in found if not plan.uses_an_index]
    click.echo(f"{len(found)} queries, {len(scanning)} without an index")


@admin.command("redact")
@click.argument("target", required=False)
@click.option("--salt", default="autozest", show_default=True, help="Changes the stand ins.")
@click.option("--dry-run", is_flag=True, help="Say what would change and write nothing.")
@pass_context
def redact_copy(ctx: Context, target: str | None, salt: str, dry_run: bool) -> None:
    """Make a copy somebody outside this building can be given.

    Rows are kept and money is never touched. A redacted file has to behave
    like the real one or it is not a reproduction of anything.
    """
    conn = ctx.open()
    if dry_run or not target:
        counts = would_change(conn)
        for column, found in sorted(counts.items()):
            click.echo(f"  {column:<34} {found}")
        click.echo(f"{sum(counts.values())} values across {len(counts)} columns")
        if not target:
            return
        return
    written = redact(conn, target, salt=salt)
    click.echo(written.summary())


@admin.command("manual")
@click.option("--out", "out_path", default=None, help="Write to a file rather than stdout.")
def manual(out_path: str | None) -> None:
    """The operator manual, written from the commands themselves.

    Generated rather than kept, because a manual that is kept is a manual that
    is wrong within a quarter.
    """
    from autozest.cli.main import cli as root

    lines = render_manual(root)
    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        click.echo(f"{len(lines)} lines to {target}")
        return
    for line in lines:
        click.echo(line)


@admin.command("doctor")
@click.option("--repair", "do_repair", is_flag=True, help="Fix the safe half.")
@pass_context
def doctor(ctx: Context, do_repair: bool) -> None:
    """What is wrong with this database, and optionally fix the safe half.

    Only the findings marked fixable are ever touched. A ledger entry that does
    not balance is not one of them, because the fix depends on which side is
    wrong and nothing here can know that.
    """
    conn = ctx.open()
    report = examine_database(conn)
    for finding in report.findings:
        click.echo(str(finding))
    click.echo(report.summary())
    if do_repair and report.repairable:
        fixed = repair_database(conn)
        for code, rows in sorted(fixed.items()):
            click.echo(f"fixed {code}: {rows} rows")
    if report.needs_a_person:
        raise SystemExit(1)


@admin.command("history")
@click.option("--since", default=None, help="Only what breaks between there and here.")
def history(since: str | None) -> None:
    """What changed in each version."""
    if since:
        from autozest.history import breaking_since

        breaks = breaking_since(since)
        for item in breaks:
            click.echo(f"breaking  {item}")
        click.echo(f"{len(breaks)} breaking changes since {since}")
        return
    for line in render_history():
        click.echo(line)
    click.echo(f"this build is {current_release().version}")
