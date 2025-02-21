"""``az consign`` and ``az inspect``.

These are the two commands the yard actually uses, and they are used by people
standing next to a truck rather than sitting at a desk. So they take whatever
identifier is to hand, they print one screen of text, and they say what
happened rather than exiting silently on success.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import click

from autozest.cli import lookup
from autozest.cli.context import Context
from autozest.consignment.importer import ConsignmentImporter
from autozest.consignment.intake import Arrival, IntakeService
from autozest.consignment.models import ConsignmentStatus
from autozest.inspection.announcements import AnnouncementCode, forced_light, labels
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.inspection.queue import InspectionQueue
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.reporting.text import Align, Column, Table, key_values, money_column

pass_context = click.make_pass_decorator(Context)

CONSIGNMENT_TABLE = Table(
    [
        Column("reference", 13),
        Column("vin", 17),
        Column("seller", 10),
        Column("status", 11),
        money_column("floor", 11),
        Column("pass", 6),
    ]
)


@click.group()
def consign() -> None:
    """Register units, receive them and manage what is on the ground."""


@consign.command("register")
@click.argument("vin")
@click.argument("seller")
@click.option("--floor", default=None, help="Reserve, as an amount.")
@click.option("--transport", default=None, help="Transport reference.")
@click.option("--no-title", is_flag=True, help="The title is not coming with the unit.")
@click.option("--note", default=None)
@pass_context
def register(
    ctx: Context,
    vin: str,
    seller: str,
    floor: str | None,
    transport: str | None,
    no_title: bool,
    note: str | None,
) -> None:
    """Book a unit into the sale."""
    service = IntakeService(ctx.open())
    unit = service.register(
        vin.upper(),
        seller,
        floor=Money.parse(floor) if floor else None,
        transport_ref=transport,
        title_expected=not no_title,
        note=note,
    )
    click.echo(f"registered {unit.id} for {unit.vin}")


@consign.command("check-in")
@click.argument("token")
@click.option("--keys", type=int, required=True, help="How many keys came with it.")
@click.option("--miles", type=int, required=True, help="What the odometer reads at the gate.")
@click.option("--note", default=None)
@pass_context
def check_in(ctx: Context, token: str, keys: int, miles: int, note: str | None) -> None:
    """Receive a unit at the gate, by reference, vin or transport paperwork."""
    service = IntakeService(ctx.open())
    unit = service.find(token)
    arrived = service.check_in(unit.id, Arrival(keys_count=keys, miles=miles, note=note))
    click.echo(f"{arrived.id} is in, gate pass {arrived.gate_pass}")


@consign.command("show")
@click.argument("token")
@pass_context
def show(ctx: Context, token: str) -> None:
    """Everything known about one unit on one screen."""
    conn = ctx.open()
    intake = IntakeService(conn)
    inspection = InspectionService(conn)
    unit = intake.find(token)
    vehicle = intake.vehicles.get(unit.vin)
    history = intake.vehicles.history(unit.vin)
    report = inspection.latest_for(unit.id)
    codes = inspection.announcements(unit.id)
    light = forced_light(codes)

    for line in key_values(
        [
            ("reference", unit.id),
            ("vehicle", vehicle.description),
            ("vin", unit.vin),
            ("seller", unit.seller_id),
            ("status", unit.status.value),
            ("floor", unit.floor),
            ("keys", unit.keys_count),
            ("gate pass", unit.gate_pass),
            ("odometer", history.latest.miles if history.latest else None),
            ("grade", report.grade if report else None),
            ("light", light.value if light else "green"),
        ]
    ):
        click.echo(line)
    if codes:
        click.echo("announcements")
        for label in labels(codes):
            click.echo(f"  {label}")


@consign.command("list")
@click.option("--status", default=None, help="Narrow to one status.")
@click.option("--seller", default=None, help="Narrow to one seller.")
@pass_context
def list_units(ctx: Context, status: str | None, seller: str | None) -> None:
    """What is booked, what is here and what has gone."""
    service = IntakeService(ctx.open())
    wanted = ConsignmentStatus(status) if status else None
    if seller:
        units = service.consignments.for_seller(seller, wanted)
    elif wanted:
        units = service.consignments.with_status(wanted)
    else:
        units = service.on_the_ground()
    rows = [[u.id, u.vin, u.seller_id, u.status.value, u.floor, u.gate_pass] for u in units]
    for line in CONSIGNMENT_TABLE.render(rows):
        click.echo(line)
    click.echo(f"{len(rows)} units")


@consign.command("floor")
@click.argument("token")
@click.argument("amount")
@pass_context
def set_floor(ctx: Context, token: str, amount: str) -> None:
    """Set or move the reserve."""
    service = IntakeService(ctx.open())
    unit = service.find(token)
    moved = service.consignments.set_floor(unit.id, Money.parse(amount))
    click.echo(f"{moved.id} floor is {moved.floor}")


@consign.command("withdraw")
@click.argument("token")
@click.argument("reason")
@pass_context
def withdraw(ctx: Context, token: str, reason: str) -> None:
    """Pull a unit out of the sale."""
    service = IntakeService(ctx.open())
    unit = service.find(token)
    click.echo(f"withdrew {service.withdraw(unit.id, reason).id}")


@click.group()
def inspect() -> None:
    """Write condition reports and sign them off."""


@inspect.command("open")
@click.argument("token")
@click.option("--inspector", required=True)
@pass_context
def open_report(ctx: Context, token: str, inspector: str) -> None:
    """Start a condition report on a unit that is through the gate."""
    conn = ctx.open()
    unit = lookup.consignment(ctx, token)
    report = InspectionService(conn).open_report(unit.id, inspector)
    click.echo(f"opened {report.id} on {unit.id}")


@inspect.command("damage")
@click.argument("report")
@click.option("--panel", required=True, type=click.Choice([p.value for p in Panel]))
@click.option("--kind", required=True, type=click.Choice([k.value for k in DamageKind]))
@click.option("--severity", required=True, type=click.IntRange(1, 5))
@click.option("--estimate", default=None, help="Override the matrix cost.")
@click.option("--note", default=None)
@pass_context
def add_damage(
    ctx: Context,
    report: str,
    panel: str,
    kind: str,
    severity: int,
    estimate: str | None,
    note: str | None,
) -> None:
    """Record one finding."""
    service = InspectionService(ctx.open())
    line = DamageLine(
        panel=Panel(panel),
        kind=DamageKind(kind),
        severity=severity,
        estimate=Money.parse(estimate) if estimate else None,
        note=note,
    )
    service.add_damage(report, line)
    click.echo(f"{report}: {line.describe()} at {line.cost}")


@inspect.command("sign")
@click.argument("report")
@click.option("--not-drivable", is_flag=True)
@click.option("--no-start", is_flag=True)
@click.option("--tread", type=click.IntRange(0, 12), default=None)
@click.option("--body-on-frame", is_flag=True, help="Not a unibody.")
@click.option("--value", default=None, help="What it would be worth clean.")
@click.option(
    "--declare", multiple=True, type=click.Choice([c.value for c in AnnouncementCode])
)
@pass_context
def sign(
    ctx: Context,
    report: str,
    not_drivable: bool,
    no_start: bool,
    tread: int | None,
    body_on_frame: bool,
    value: str | None,
    declare: tuple[str, ...],
) -> None:
    """Sign the report off, which grades the unit and writes the calls."""
    service = InspectionService(ctx.open())
    signed, codes = service.finalise(
        report,
        drivable=not not_drivable,
        engine_starts=not no_start,
        tread_32nds=tread,
        unibody=not body_on_frame,
        reference_value=Money.parse(value) if value else None,
        declared=frozenset(AnnouncementCode(c) for c in declare),
    )
    grade: Decimal | None = signed.grade
    click.echo(f"{report} signed at grade {grade}")
    for label in labels(codes):
        click.echo(f"  {label}")


@inspect.command("damage-list")
@click.argument("report")
@pass_context
def damage_list(ctx: Context, report: str) -> None:
    """Every finding on a report and what it adds up to."""
    service = InspectionService(ctx.open())
    lines = service.damage(report)
    table = Table(
        [
            Column("panel", 20),
            Column("kind", 14),
            Column("sev", 3, Align.RIGHT),
            money_column("cost", 11),
            Column("structural", 10),
        ]
    )
    rows = [
        [line.panel.value, line.kind.value, line.severity, line.cost, line.is_structural]
        for line in lines
    ]
    for text in table.render(rows):
        click.echo(text)
    click.echo(
        f"{len(lines)} findings, {Money(sum(line.cost.cents for line in lines))} of repairs"
    )


@consign.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.argument("seller")
@click.option("--dry-run", is_flag=True, help="Check the file and write nothing.")
@pass_context
def import_file(ctx: Context, path: str, seller: str, dry_run: bool) -> None:
    """Load a fleet's spreadsheet.

    Nothing is written unless the whole file is clean, so a dry run and a real
    run report exactly the same problems.
    """
    importer = ConsignmentImporter(ctx.open())
    plan = importer.plan(Path(path).read_text("utf-8"), seller)
    for problem in plan.report():
        click.echo(problem, err=True)
    if not plan.is_clean:
        click.echo(f"{len(plan.problems)} problems, nothing imported", err=True)
        raise SystemExit(2)
    if dry_run:
        click.echo(f"{plan.count} rows would import cleanly")
        return
    created = importer.apply(plan)
    click.echo(f"{len(created)} units registered for {seller}")
    for unit in created[:10]:
        click.echo(f"  {unit.id}  {unit.vin}")
    if len(created) > 10:
        click.echo(f"  and {len(created) - 10} more")


@consign.command("history")
@click.argument("vin")
@pass_context
def history(ctx: Context, vin: str) -> None:
    """Every time this unit has been through, and what its odometer said."""
    service = IntakeService(ctx.open())
    key = vin.strip().upper()
    vehicle = service.vehicles.get(key)
    click.echo(f"{vehicle.description}  {key}")
    table = Table(
        [
            Column("consignment", 13),
            Column("seller", 10),
            Column("status", 11),
            money_column("floor", 11),
        ]
    )
    visits = service.consignments.history_for_vin(key)
    rows = [[c.id, c.seller_id, c.status.value, c.floor] for c in visits]
    for line in table.render(rows):
        click.echo(line)
    readings = service.vehicles.history(key).readings
    click.echo(f"{len(visits)} visits, {len(readings)} odometer readings")
    for reading in readings:
        click.echo(f"  {reading.read_on}  {reading.miles:>9}  {reading.source}")


@inspect.command("queue")
@click.option("--limit", default=20, show_default=True)
@pass_context
def queue(ctx: Context, limit: int) -> None:
    """What to inspect next, in the order it costs least to leave.

    Units already in a planned sale come first. An uninspected unit in a sale
    is a unit that gets pulled.
    """
    service = InspectionQueue(ctx.open(), ctx.policies.calendar)
    built = service.build()
    for item in built.waiting[:limit]:
        click.echo(f"  {item.line()}")
    click.echo(built.summary())
